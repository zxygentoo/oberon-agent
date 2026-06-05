//! Serial transport over a PTY (raw mode) or a FIFO pair.
//!
//! Uses `std::io::{Read, Write}` via the stable `&File` impls for the bulk
//! transfer. Raw `libc` only for the two operations std doesn't cover:
//! `poll` (timed read availability) and `tcsetattr` (raw-mode the PTY).
//! `Transport` is the real `protocol::Request`: it moves the bytes; the
//! frame grammar itself lives in protocol.rs.

use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::io::{AsRawFd, RawFd};
use std::path::Path;
use std::ptr::{addr_of, addr_of_mut};
use std::time::Duration;

use crate::error::{Error, Result};
use crate::protocol::{read_response, Request, Response};

pub struct Transport {
    reader: File,
    /// `None` when reader and writer share an fd (PTY mode); writes go to
    /// `reader`. `Some` for FIFO mode, where the two directions are distinct.
    writer: Option<File>,
    timeout: Duration,
}

impl Request for Transport {
    fn send(&self, frame: &[u8]) -> Result<Response> {
        // Send. FIFO/PTY write buffers (tens of KB) absorb any module-sized
        // payload, so a plain blocking write is safe — no flow control needed.
        // `&File: Write` is the stable std impl that lets us write without &mut.
        let mut w: &File = self.writer.as_ref().unwrap_or(&self.reader);
        w.write_all(frame)?;

        // Receive into caller-allocated buffers, polling the fd for each read.
        read_response(|buf| self.recv_exact(buf))
    }
}

impl Transport {
    fn recv_exact(&self, buf: &mut [u8]) -> Result<()> {
        let want = buf.len();
        let mut filled = 0;
        while filled < want {
            if !poll_readable(self.reader.as_raw_fd(), self.timeout)? {
                return Err(Error::Timeout {
                    secs: self.timeout.as_secs_f64(),
                    got: filled,
                    want,
                });
            }
            // `&File: Read` is the matching read impl; binding to `mut r`
            // lets the method's `&mut self` autoref.
            let mut r: &File = &self.reader;
            match r.read(&mut buf[filled..]) {
                Ok(0) => return Err(Error::Eof),
                Ok(n) => filled += n,
                Err(e) if e.kind() != io::ErrorKind::Interrupted => {
                    return Err(Error::Io(e));
                }
                // EINTR: fall through and retry.
                Err(_) => {}
            }
        }
        Ok(())
    }
}

pub fn open_path(path: &Path, timeout: Duration) -> Result<Transport> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .custom_flags(libc::O_NOCTTY)
        .open(path)
        .map_err(|source| Error::OpenSerial {
            path: path.to_path_buf(),
            source,
        })?;
    set_raw_mode(&file).map_err(|source| Error::OpenSerial {
        path: path.to_path_buf(),
        source,
    })?;
    Ok(Transport {
        reader: file,
        writer: None,
        timeout,
    })
}

pub fn open_fifos(in_path: &Path, out_path: &Path, timeout: Duration) -> Result<Transport> {
    let writer = open_fifo(in_path)?;
    let reader = open_fifo(out_path)?;
    Ok(Transport {
        reader,
        writer: Some(writer),
        timeout,
    })
}

fn open_fifo(path: &Path) -> Result<File> {
    // O_RDWR avoids the open-blocking dance: a FIFO opened read-only blocks
    // until a writer attaches, and vice versa. We just want a non-blocking
    // open of either end.
    OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(|source| Error::OpenFifo {
            path: path.to_path_buf(),
            source,
        })
}

fn set_raw_mode(file: &File) -> io::Result<()> {
    let fd = file.as_raw_fd();
    let mut t: libc::termios = unsafe { std::mem::zeroed() };
    // SAFETY: `fd` is a tty/serial fd we just opened; `t` is a fresh termios
    // we own, sized correctly for the libc calls.
    unsafe {
        if libc::tcgetattr(fd, addr_of_mut!(t)) != 0 {
            return Err(io::Error::last_os_error());
        }
        libc::cfmakeraw(addr_of_mut!(t));
        if libc::tcsetattr(fd, libc::TCSANOW, addr_of!(t)) != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

fn poll_readable(fd: RawFd, timeout: Duration) -> Result<bool> {
    let ms: i32 = i32::try_from(timeout.as_millis()).unwrap_or(i32::MAX);
    let mut pfd = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    loop {
        // SAFETY: `pfd` is a single owned pollfd; `n=1` matches the buffer length.
        let r = unsafe { libc::poll(addr_of_mut!(pfd), 1, ms) };
        if r >= 0 {
            return Ok(r > 0);
        }
        let e = io::Error::last_os_error();
        if e.kind() != io::ErrorKind::Interrupted {
            return Err(Error::Io(e));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{encode_response, Request, Status};
    use std::os::unix::io::FromRawFd;

    fn pipe() -> (File, File) {
        let mut fds = [0; 2];
        // SAFETY: `fds` is the 2-slot buffer `pipe` requires; on success both
        // fds are fresh, and each is owned by exactly one of the Files below.
        assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0, "pipe failed");
        // SAFETY: valid fds we just created, not owned elsewhere.
        unsafe { (File::from_raw_fd(fds[0]), File::from_raw_fd(fds[1])) }
    }

    /// Transport wired to two in-memory pipes. Returns `(transport, feed,
    /// sent)`: write the device's response bytes into `feed`; read what the
    /// transport sent out of `sent`.
    fn harness(timeout: Duration) -> (Transport, File, File) {
        let (resp_read, resp_write) = pipe();
        let (sent_read, sent_write) = pipe();
        let t = Transport {
            reader: resp_read,
            writer: Some(sent_write),
            timeout,
        };
        (t, resp_write, sent_read)
    }

    #[test]
    fn send_writes_frame_and_decodes_response() {
        let (t, mut feed, mut sent) = harness(Duration::from_secs(1));
        feed.write_all(&encode_response(Status::Ok, b"hi")).unwrap();
        let r = t.send(b"frame").unwrap();
        assert_eq!(r.status, Status::Ok);
        assert_eq!(r.payload, b"hi");
        // The request frame went out verbatim — transport doesn't inspect it.
        let mut buf = [0u8; 5];
        sent.read_exact(&mut buf).unwrap();
        assert_eq!(&buf, b"frame");
    }

    #[test]
    fn silent_line_times_out_with_progress_count() {
        // `_feed` stays open so the read end blocks instead of seeing EOF.
        let (t, _feed, _sent) = harness(Duration::from_millis(30));
        match t.send(b"x") {
            Err(Error::Timeout { got: 0, want: 1, .. }) => {}
            other => panic!("expected timeout, got {other:?}"),
        }
    }

    #[test]
    fn closed_line_is_eof() {
        let (t, feed, _sent) = harness(Duration::from_secs(1));
        drop(feed);
        assert!(matches!(t.send(b"x"), Err(Error::Eof)));
    }

    #[test]
    fn response_split_across_writes_is_reassembled() {
        let (t, mut feed, _sent) = harness(Duration::from_secs(1));
        // Split mid-length-field so recv_exact has to loop within one buffer.
        let frame = encode_response(Status::Ok, b"abc");
        let writer = std::thread::spawn(move || {
            feed.write_all(&frame[..4]).unwrap();
            std::thread::sleep(Duration::from_millis(20));
            feed.write_all(&frame[4..]).unwrap();
        });
        let r = t.send(b"x").unwrap();
        assert_eq!(r.payload, b"abc");
        writer.join().unwrap();
    }
}
