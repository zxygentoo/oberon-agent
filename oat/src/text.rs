//! Host <-> Oberon text conversion: LF <-> CR plus 0F1H Texts-header strip.
//!
//! Canonical host text is LF; Oberon source is plain ASCII with CR line
//! separators. Files written by Oberon's editor (`Texts.Close`) carry a
//! 0F1X-tagged formatted header — strip it to recover the plain character run.

const TEXT_TAG: u8 = 0xF1;

/// Host LF text -> Oberon CR-separated bytes (plain ASCII).
pub fn to_oberon(text: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len());
    let mut bytes = text.bytes().peekable();
    while let Some(b) = bytes.next() {
        match b {
            b'\r' => {
                // Collapse \r\n into a single CR (drop the following \n).
                if bytes.peek() == Some(&b'\n') {
                    bytes.next();
                }
                out.push(b'\r');
            }
            b'\n' => out.push(b'\r'),
            other => out.push(other),
        }
    }
    out
}

/// Oberon file bytes -> host LF text. Strips a 0F1X formatted header if present.
pub fn from_oberon(data: &[u8]) -> String {
    let body = if data.first() == Some(&TEXT_TAG) {
        strip_text_header(data)
    } else {
        data
    };
    let mut out = String::with_capacity(body.len());
    let mut bytes = body.iter().copied().peekable();
    while let Some(b) = bytes.next() {
        match b {
            b'\r' => {
                if bytes.peek() == Some(&b'\n') {
                    bytes.next();
                }
                out.push('\n');
            }
            other => out.push(other as char),
        }
    }
    out
}

fn strip_text_header(data: &[u8]) -> &[u8] {
    // Layout (Texts.Store after the 1-byte tag): off:4 LE = absolute file
    // offset of the character run. Fall back to the raw bytes if the header
    // is too short or `off` is out of range.
    let header: Option<[u8; 4]> = data.get(1..5).and_then(|s| s.try_into().ok());
    let Some(bytes) = header else { return data };
    let Ok(off) = usize::try_from(i32::from_le_bytes(bytes)) else {
        return data;
    };
    if (5..=data.len()).contains(&off) {
        &data[off..]
    } else {
        data
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn to_oberon_translates_lf_and_crlf_to_cr() {
        assert_eq!(to_oberon("a\nb\n"), b"a\rb\r");
        assert_eq!(to_oberon("a\r\nb\r\n"), b"a\rb\r");
        assert_eq!(to_oberon(""), b"");
    }

    #[test]
    fn from_oberon_translates_cr_to_lf() {
        assert_eq!(from_oberon(b"a\rb\r"), "a\nb\n");
        assert_eq!(from_oberon(b"a\r\nb"), "a\nb");
    }

    #[test]
    fn roundtrip_preserves_ascii_text() {
        let s = "MODULE M;\nBEGIN\n  x := 1\nEND M.\n";
        assert_eq!(from_oberon(&to_oberon(s)), s);
    }

    #[test]
    fn from_oberon_strips_text_header() {
        // tag=F1, off=5 (LE), then plain "hi"
        let data = [0xF1, 0x05, 0x00, 0x00, 0x00, b'h', b'i'];
        assert_eq!(from_oberon(&data), "hi");
    }

    #[test]
    fn from_oberon_leaves_garbled_header_intact() {
        // off=42 is out of range — fall back to treating the whole buffer as text.
        let data = [0xF1, 0x2A, 0x00, 0x00, 0x00, b'x'];
        assert!(from_oberon(&data).contains('x'));
    }

    #[test]
    fn from_oberon_truncated_header_falls_back() {
        // Tag byte present but fewer than 4 offset bytes follow — the whole
        // buffer is treated as text rather than panicking or truncating.
        // (Count chars, not bytes: high bytes map Latin-1-style to one char
        // each, which is more than one UTF-8 byte.)
        assert_eq!(from_oberon(&[TEXT_TAG, 0x05]).chars().count(), 2);
        assert_eq!(from_oberon(&[TEXT_TAG]).chars().count(), 1);
    }
}
