//! Wire framing for the `PUT`/`GET`/`CALL` protocol that `oat` speaks to
//! `AgentTool.Mod` on the device.
//!
//! Host is master: build a REQUEST, send it, then read one RESPONSE. All
//! multi-byte integers are unsigned little-endian.
//!
//! This module is the shared vocabulary of the crate's layering:
//! `tools` (semantics) and `transport` (I/O) both depend on it and on
//! nothing else of each other — `Request` is the seam between them.

use crate::error::{Error, Result};

/// The seam between the typed world and the fd world: send one encoded
/// REQUEST frame, get back the decoded RESPONSE. `transport::Transport` is
/// the real implementation; tools.rs tests plug in an in-memory fake.
pub trait Request {
    fn send(&self, frame: &[u8]) -> Result<Response>;
}

pub const SYNC_REQ: u8 = 0xA5;
pub const SYNC_RESP: u8 = 0x5A;

pub const OP_PUT: u8 = 1;
pub const OP_GET: u8 = 2;
pub const OP_CALL: u8 = 3;

pub const ST_OK: u8 = 0;
pub const ST_NOT_FOUND: u8 = 1;
pub const ST_TRAPPED: u8 = 2;
#[allow(dead_code)] // documented wire vocabulary; not used in current paths.
pub const ST_ERROR: u8 = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u8,
    pub payload: Vec<u8>,
}

impl Response {
    pub fn ok(&self) -> bool {
        self.status == ST_OK
    }
}

pub fn build_put(name: &str, data: &[u8]) -> Result<Vec<u8>> {
    let name = name_field(name)?;
    let len = u32::try_from(data.len()).unwrap_or(u32::MAX).to_le_bytes();
    let mut frame = Vec::with_capacity(2 + name.len() + 4 + data.len());
    frame.extend_from_slice(&[SYNC_REQ, OP_PUT]);
    frame.extend_from_slice(&name);
    frame.extend_from_slice(&len);
    frame.extend_from_slice(data);
    Ok(frame)
}

pub fn build_get(name: &str) -> Result<Vec<u8>> {
    let name = name_field(name)?;
    let mut frame = Vec::with_capacity(2 + name.len());
    frame.extend_from_slice(&[SYNC_REQ, OP_GET]);
    frame.extend_from_slice(&name);
    Ok(frame)
}

pub fn build_call(cmd: &str, par: &[u8]) -> Result<Vec<u8>> {
    let name = name_field(cmd)?;
    let len = u32::try_from(par.len()).unwrap_or(u32::MAX).to_le_bytes();
    let mut frame = Vec::with_capacity(2 + name.len() + 4 + par.len());
    frame.extend_from_slice(&[SYNC_REQ, OP_CALL]);
    frame.extend_from_slice(&name);
    frame.extend_from_slice(&len);
    frame.extend_from_slice(par);
    Ok(frame)
}

fn name_field(name: &str) -> Result<Vec<u8>> {
    let bytes = name.as_bytes();
    let len = u8::try_from(bytes.len()).map_err(|_| Error::BadName {
        name: name.to_string(),
        len: bytes.len(),
    })?;
    if len == 0 {
        return Err(Error::BadName {
            name: name.to_string(),
            len: 0,
        });
    }
    let mut out = Vec::with_capacity(1 + bytes.len());
    out.push(len);
    out.extend_from_slice(bytes);
    Ok(out)
}

/// Read one RESPONSE frame, filling caller-provided buffers via `recv`.
///
/// The closure form lets the small fixed-size reads (sync, status, length)
/// land on stack-allocated arrays rather than allocating a `Vec` per call.
pub fn read_response<F>(mut recv: F) -> Result<Response>
where
    F: FnMut(&mut [u8]) -> Result<()>,
{
    let mut sync = [0u8; 1];
    recv(&mut sync)?;
    if sync[0] != SYNC_RESP {
        return Err(Error::BadSync {
            got: sync[0],
            expected: SYNC_RESP,
        });
    }
    let mut status = [0u8; 1];
    recv(&mut status)?;
    let mut len = [0u8; 4];
    recv(&mut len)?;
    let length = u32::from_le_bytes(len) as usize;
    let mut payload = vec![0u8; length];
    if length > 0 {
        recv(&mut payload)?;
    }
    Ok(Response {
        status: status[0],
        payload,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_get_shapes_correctly() {
        let f = build_get("M.Mod").unwrap();
        assert_eq!(f, &[SYNC_REQ, OP_GET, 5, b'M', b'.', b'M', b'o', b'd']);
    }

    #[test]
    fn build_put_includes_le_length_and_data() {
        let f = build_put("X", b"hi").unwrap();
        assert_eq!(f, &[SYNC_REQ, OP_PUT, 1, b'X', 2, 0, 0, 0, b'h', b'i']);
    }

    #[test]
    fn build_call_includes_le_length_and_par() {
        let f = build_call("A.B", b"p").unwrap();
        assert_eq!(
            f,
            &[SYNC_REQ, OP_CALL, 3, b'A', b'.', b'B', 1, 0, 0, 0, b'p']
        );
    }

    #[test]
    fn name_field_rejects_empty_and_too_long() {
        assert!(build_get("").is_err());
        assert!(build_get(&"x".repeat(256)).is_err());
    }

    #[test]
    fn read_response_parses_ok_with_payload() {
        let bytes = vec![SYNC_RESP, ST_OK, 3, 0, 0, 0, b'a', b'b', b'c'];
        let r = read_with(&bytes).unwrap();
        assert_eq!(r.status, ST_OK);
        assert_eq!(r.payload, b"abc");
        assert!(r.ok());
    }

    #[test]
    fn read_response_handles_empty_payload() {
        let r = read_with(&[SYNC_RESP, ST_OK, 0, 0, 0, 0]).unwrap();
        assert!(r.payload.is_empty());
    }

    #[test]
    fn read_response_rejects_bad_sync() {
        let err = read_with(&[0x42, ST_OK, 0, 0, 0, 0]).unwrap_err();
        assert!(matches!(err, Error::BadSync { got: 0x42, .. }));
    }

    fn read_with(bytes: &[u8]) -> Result<Response> {
        let mut cur = 0;
        read_response(|buf| {
            buf.copy_from_slice(&bytes[cur..cur + buf.len()]);
            cur += buf.len();
            Ok(())
        })
    }
}
