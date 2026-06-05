//! Wire framing for the `PUT`/`GET`/`CALL`/`EDIT` protocol that `oat` speaks
//! to `AgentTool.Mod` on the device.
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

// The raw wire encoding never leaves this module: production code only
// encodes requests (build_*) and decodes responses (read_response); test
// fakes that play the device go through parse_request / encode_response.
const SYNC_REQ: u8 = 0xA5;
const SYNC_RESP: u8 = 0x5A;

const OP_PUT: u8 = 1;
const OP_GET: u8 = 2;
const OP_CALL: u8 = 3;
const OP_EDIT: u8 = 4;

/// Device status of a RESPONSE. The wire byte is an encoding detail private
/// to this module — upper layers match on variants. `Other` carries status
/// bytes this build doesn't know (newer device), kept for diagnostics.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    NotFound,
    Trapped,
    Error,
    /// EDIT: OLD does not occur in the file.
    NoMatch,
    /// EDIT: OLD occurs more than once; payload = occurrence count (u32 LE).
    NotUnique,
    /// A status byte this build doesn't know.
    Other(u8),
}

impl Status {
    fn from_byte(b: u8) -> Self {
        match b {
            0 => Self::Ok,
            1 => Self::NotFound,
            2 => Self::Trapped,
            3 => Self::Error,
            4 => Self::NoMatch,
            5 => Self::NotUnique,
            b => Self::Other(b),
        }
    }

    /// The wire byte — for error messages and tests that craft raw frames.
    pub const fn byte(self) -> u8 {
        match self {
            Self::Ok => 0,
            Self::NotFound => 1,
            Self::Trapped => 2,
            Self::Error => 3,
            Self::NoMatch => 4,
            Self::NotUnique => 5,
            Self::Other(b) => b,
        }
    }
}

/// Longest OLD fragment (in device bytes, after LF -> CR conversion) that an
/// EDIT frame may carry — the device matches inside a fixed buffer. Keep in
/// sync with `editLim` in `Mod/*/AgentTool.Mod`. tools.rs falls back to the
/// GET+PUT path for anything longer.
pub const EDIT_OLD_LIMIT: usize = 1024;

#[derive(Debug, PartialEq, Eq)]
pub struct Response {
    pub status: Status,
    pub payload: Vec<u8>,
}

impl Response {
    pub fn ok(&self) -> bool {
        self.status == Status::Ok
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

pub fn build_edit(name: &str, old: &[u8], new: &[u8]) -> Result<Vec<u8>> {
    let name = name_field(name)?;
    let old_len = u32::try_from(old.len()).unwrap_or(u32::MAX).to_le_bytes();
    let new_len = u32::try_from(new.len()).unwrap_or(u32::MAX).to_le_bytes();
    let mut frame = Vec::with_capacity(2 + name.len() + 8 + old.len() + new.len());
    frame.extend_from_slice(&[SYNC_REQ, OP_EDIT]);
    frame.extend_from_slice(&name);
    frame.extend_from_slice(&old_len);
    frame.extend_from_slice(old);
    frame.extend_from_slice(&new_len);
    frame.extend_from_slice(new);
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
        status: Status::from_byte(status[0]),
        payload,
    })
}

// --- test-side wire helpers ---------------------------------------------
// The device's half of the codec, for test fakes that play the device and
// for transport tests that feed raw frames through the real decoder.
// Compiled only under cfg(test): production code never parses requests.

/// A REQUEST frame as the device sees it after parsing.
#[cfg(test)]
#[derive(Debug, PartialEq, Eq)]
pub enum ParsedRequest {
    Put { name: String, data: Vec<u8> },
    Get { name: String },
    Call { cmd: String, par: Vec<u8> },
    Edit { name: String, old: Vec<u8>, new: Vec<u8> },
}

/// Parse a REQUEST frame. Panics on malformed input — test code.
#[cfg(test)]
pub fn parse_request(frame: &[u8]) -> ParsedRequest {
    assert_eq!(frame[0], SYNC_REQ, "bad request sync");
    let op = frame[1];
    let nlen = frame[2] as usize;
    let name = std::str::from_utf8(&frame[3..3 + nlen]).unwrap().to_string();
    let mut i = 3 + nlen;
    let mut blob = || {
        let len = u32::from_le_bytes(frame[i..i + 4].try_into().unwrap()) as usize;
        i += 4;
        let b = frame[i..i + len].to_vec();
        i += len;
        b
    };
    match op {
        OP_PUT => ParsedRequest::Put { name, data: blob() },
        OP_GET => ParsedRequest::Get { name },
        OP_CALL => ParsedRequest::Call { cmd: name, par: blob() },
        OP_EDIT => ParsedRequest::Edit {
            name,
            old: blob(),
            new: blob(),
        },
        op => panic!("bad op {op}"),
    }
}

/// Encode a RESPONSE frame exactly as the device would.
#[cfg(test)]
pub fn encode_response(status: Status, payload: &[u8]) -> Vec<u8> {
    let len = u32::try_from(payload.len()).unwrap().to_le_bytes();
    let mut frame = Vec::with_capacity(6 + payload.len());
    frame.extend_from_slice(&[SYNC_RESP, status.byte()]);
    frame.extend_from_slice(&len);
    frame.extend_from_slice(payload);
    frame
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
    fn build_edit_carries_both_length_prefixed_fragments() {
        let f = build_edit("X", b"ab", b"c").unwrap();
        assert_eq!(
            f,
            &[SYNC_REQ, OP_EDIT, 1, b'X', 2, 0, 0, 0, b'a', b'b', 1, 0, 0, 0, b'c']
        );
    }

    #[test]
    fn build_edit_allows_empty_new() {
        let f = build_edit("X", b"a", b"").unwrap();
        assert_eq!(f, &[SYNC_REQ, OP_EDIT, 1, b'X', 1, 0, 0, 0, b'a', 0, 0, 0, 0]);
    }

    #[test]
    fn name_field_rejects_empty_and_too_long() {
        assert!(build_get("").is_err());
        assert!(build_get(&"x".repeat(256)).is_err());
    }

    #[test]
    fn read_response_parses_ok_with_payload() {
        let bytes = vec![SYNC_RESP, Status::Ok.byte(), 3, 0, 0, 0, b'a', b'b', b'c'];
        let r = read_with(&bytes).unwrap();
        assert_eq!(r.status, Status::Ok);
        assert_eq!(r.payload, b"abc");
        assert!(r.ok());
    }

    #[test]
    fn read_response_handles_empty_payload() {
        let r = read_with(&[SYNC_RESP, Status::Ok.byte(), 0, 0, 0, 0]).unwrap();
        assert!(r.payload.is_empty());
    }

    #[test]
    fn read_response_rejects_bad_sync() {
        let err = read_with(&[0x42, Status::Ok.byte(), 0, 0, 0, 0]).unwrap_err();
        assert!(matches!(err, Error::BadSync { got: 0x42, .. }));
    }

    #[test]
    fn status_byte_roundtrips() {
        for b in 0..=255 {
            assert_eq!(Status::from_byte(b).byte(), b);
        }
        // Unknown bytes are preserved, not collapsed.
        assert_eq!(Status::from_byte(0x2A), Status::Other(0x2A));
    }

    #[test]
    fn parse_request_inverts_the_builders() {
        assert_eq!(
            parse_request(&build_get("M.Mod").unwrap()),
            ParsedRequest::Get {
                name: "M.Mod".into()
            }
        );
        assert_eq!(
            parse_request(&build_put("X", b"hi").unwrap()),
            ParsedRequest::Put {
                name: "X".into(),
                data: b"hi".to_vec()
            }
        );
        assert_eq!(
            parse_request(&build_call("A.B", b"p").unwrap()),
            ParsedRequest::Call {
                cmd: "A.B".into(),
                par: b"p".to_vec()
            }
        );
        assert_eq!(
            parse_request(&build_edit("X", b"ab", b"").unwrap()),
            ParsedRequest::Edit {
                name: "X".into(),
                old: b"ab".to_vec(),
                new: Vec::new()
            }
        );
    }

    #[test]
    fn read_response_inverts_encode_response() {
        let r = read_with(&encode_response(Status::NotUnique, &2u32.to_le_bytes())).unwrap();
        assert_eq!(r.status, Status::NotUnique);
        assert_eq!(r.payload, 2u32.to_le_bytes());
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
