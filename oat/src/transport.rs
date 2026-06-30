//! Serial transport over a PTY (raw mode) or a FIFO pair.
//!
//! Uses `std::io::{Read, Write}` via the stable `&File` impls for the bulk
//! transfer, and `rustix` for the two syscalls std doesn't cover: `poll`
//! (timed read availability) and termios raw mode — safe wrappers, so the
//! crate as a whole can forbid unsafe. `Transport` is the real
//! `protocol::Request`: it moves the bytes; the frame grammar itself lives
//! in protocol.rs.

use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::Path;
use std::time::Duration;

use rustix::event::{self, PollFd, PollFlags, Timespec};
use rustix::fs::{Mode, OFlags};
use rustix::termios::{self, OptionalActions};

use crate::error::{Error, Result};
use crate::protocol::{read_response, Request, Response};

pub struct Transport {
    reader: File,
    /// `None` when reader and writer share an fd (PTY mode); writes go to
    /// `reader`. `Some` for FIFO mode, where the two directions are distinct.
    writer: Option<File>,
    timeout: Duration,
    /// Inter-byte send delay ("character delay"). Zero = send the frame in one
    /// `write_all` (the FIFO/emulator path, lossless). Nonzero = pace the bytes
    /// out one at a time, so the real UART peer — a single-byte register with no
    /// flow control (the OberonStation RS232R), read by a cooperative poll — can
    /// grab each byte before the next overruns it. Required for back-to-back
    /// requests on a real line: without it a request frame's first byte is lost
    /// and the frame desyncs. (The device buffers a bulk PUT payload itself, but
    /// not the request frame.) See cli.rs `--char-delay-us` (default 1000).
    char_delay: Duration,
}

impl Request for Transport {
    fn send(&self, frame: &[u8]) -> Result<Response> {
        // Drop any stale bytes left in the OS/driver buffer by a previous
        // exchange (a late or lost response on a real UART) so this request's
        // reply is read frame-aligned, not shifted by leftovers.
        self.drain_stale();

        // Send. FIFO/PTY write buffers (tens of KB) absorb any module-sized
        // payload, so a plain blocking write is safe — no flow control needed.
        // `&File: Write` is the stable std impl that lets us write without &mut.
        let mut w: &File = self.writer.as_ref().unwrap_or(&self.reader);
        if self.char_delay.is_zero() {
            w.write_all(frame)?;
        } else {
            // One byte at a time, idling the wire `char_delay` between them so the
            // device's cooperative poll can grab each byte before the next overruns
            // its single-byte register. Slow but lossless on a raw UART.
            for &b in frame {
                w.write_all(&[b])?;
                std::thread::sleep(self.char_delay);
            }
        }

        // Receive into caller-allocated buffers, polling the fd for each read.
        read_response(|buf| self.recv_exact(buf))
    }
}

impl Transport {
    /// Non-blocking: read and discard whatever is already buffered, so a stale
    /// or partial response from a prior exchange can't desync this one. Best
    /// effort — errors just stop the drain.
    fn drain_stale(&self) {
        let mut scratch = [0u8; 256];
        while poll_readable(&self.reader, Duration::ZERO).unwrap_or(false) {
            let mut r: &File = &self.reader;
            match r.read(&mut scratch) {
                Ok(n) if n > 0 => continue,
                _ => break,
            }
        }
    }

    fn recv_exact(&self, buf: &mut [u8]) -> Result<()> {
        let want = buf.len();
        let mut filled = 0;
        while filled < want {
            if !poll_readable(&self.reader, self.timeout)? {
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

pub fn open_path(
    path: &Path,
    timeout: Duration,
    baud: u32,
    char_delay: Duration,
) -> Result<Transport> {
    let open_err = |source: rustix::io::Errno| Error::OpenSerial {
        path: path.to_path_buf(),
        source: source.into(),
    };
    let fd =
        rustix::fs::open(path, OFlags::RDWR | OFlags::NOCTTY, Mode::empty()).map_err(open_err)?;
    let file = File::from(fd);
    set_raw_mode(&file, baud).map_err(open_err)?;
    Ok(Transport {
        reader: file,
        writer: None,
        timeout,
        char_delay,
    })
}

pub fn open_fifos(in_path: &Path, out_path: &Path, timeout: Duration) -> Result<Transport> {
    let writer = open_fifo(in_path)?;
    let reader = open_fifo(out_path)?;
    Ok(Transport {
        reader,
        writer: Some(writer),
        timeout,
        char_delay: Duration::ZERO, // FIFO is lossless; no pacing needed.
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

fn set_raw_mode(file: &File, baud: u32) -> rustix::io::Result<()> {
    let mut t = termios::tcgetattr(file)?;
    t.make_raw();
    // make_raw() gives raw mode + CS8/no-parity (8N1) but leaves the line speed
    // untouched — on a real UART that means whatever the port last had (often not
    // ours). Pin it to the requested baud so a real serial device matches the FPGA's
    // RS232 (19200 by default). FIFO transports skip this path entirely.
    t.set_speed(baud)?;
    termios::tcsetattr(file, OptionalActions::Now, &t)
}

fn poll_readable(file: &File, timeout: Duration) -> Result<bool> {
    let ts = Timespec::try_from(timeout).unwrap_or(Timespec {
        tv_sec: i64::MAX,
        tv_nsec: 0,
    });
    let mut fds = [PollFd::new(file, PollFlags::IN)];
    match rustix::io::retry_on_intr(|| event::poll(&mut fds, Some(&ts))) {
        Ok(n) => Ok(n > 0),
        Err(e) => Err(Error::Io(e.into())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{encode_response, Request, Status};
    use std::os::fd::OwnedFd;

    fn pipe() -> (File, File) {
        let (r, w) = io::pipe().expect("pipe");
        (File::from(OwnedFd::from(r)), File::from(OwnedFd::from(w)))
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
            char_delay: Duration::ZERO,
        };
        (t, resp_write, sent_read)
    }

    #[test]
    fn send_writes_frame_and_decodes_response() {
        let (t, mut feed, mut sent) = harness(Duration::from_secs(1));
        // Deliver the response *after* send() runs: send() now flushes the line with
        // drain_stale() first, so a reply pre-loaded before the request would be
        // (correctly) discarded — a real peer only answers once it has the request.
        // (The split-response test below does the same.)
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(20));
            feed.write_all(&encode_response(Status::Ok, b"hi")).unwrap();
        });
        let r = t.send(b"frame").unwrap();
        assert_eq!(r.status, Status::Ok);
        assert_eq!(r.payload, b"hi");
        // The request frame went out verbatim — transport doesn't inspect it.
        let mut buf = [0u8; 5];
        sent.read_exact(&mut buf).unwrap();
        assert_eq!(&buf, b"frame");
        writer.join().unwrap();
    }

    #[test]
    fn silent_line_times_out_with_progress_count() {
        // `_feed` stays open so the read end blocks instead of seeing EOF.
        let (t, _feed, _sent) = harness(Duration::from_millis(30));
        match t.send(b"x") {
            Err(Error::Timeout {
                got: 0, want: 1, ..
            }) => {}
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
