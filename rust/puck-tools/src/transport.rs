//! Serial transport over a PTY (raw mode) or a FIFO pair.
//!
//! Uses `std::io::{Read, Write}` via the stable `&File` impls for the bulk
//! transfer. Raw `libc` only for the two operations std doesn't cover:
//! `poll` (timed read availability) and `tcsetattr` (raw-mode the PTY).
//! `Wire` is the seam tools.rs codes against — tests plug in an in-memory
//! fake without touching real fds.

use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::io::{AsRawFd, RawFd};
use std::path::Path;
use std::ptr::{addr_of, addr_of_mut};
use std::time::Duration;

use crate::error::{Error, Result};
use crate::protocol::{read_response, Response};

/// The single seam tools.rs codes against: send a request frame, get a response.
pub trait Wire {
    fn request(&self, frame: &[u8]) -> Result<Response>;
}

pub struct Transport {
    reader: File,
    /// `None` when reader and writer share an fd (PTY mode); writes go to
    /// `reader`. `Some` for FIFO mode, where the two directions are distinct.
    writer: Option<File>,
    timeout: Duration,
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

impl Wire for Transport {
    fn request(&self, frame: &[u8]) -> Result<Response> {
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
