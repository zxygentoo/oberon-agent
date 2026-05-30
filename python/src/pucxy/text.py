"""Oberon <-> host text conversion. See spec.md sections 4.3-4.4.

Canonical host text is LF. Oberon source is plain ASCII with CR line separators.
Files written by Oberon's editor (Texts.Close) carry a 0F1X-tagged formatted
header; we strip it to the plain run (the `ob2unix` operation).
"""

TEXT_TAG = 0xF1


def to_oberon(text: str) -> bytes:
    """Host LF text -> plain-ASCII Oberon bytes (CR line separators)."""
    return text.replace("\r\n", "\n").replace("\n", "\r").encode("latin1", "replace")


def from_oberon(data: bytes) -> str:
    """Oberon file bytes -> host LF text. Strips a 0F1X header if present."""
    if data[:1] == bytes([TEXT_TAG]):
        data = _strip_text_header(data)
    return data.decode("latin1").replace("\r\n", "\n").replace("\r", "\n")


def _strip_text_header(data: bytes) -> bytes:
    """Recover the character run from a formatted (0F1X) Texts file.

    Layout (Texts.Store, after the 1-byte tag written by Texts.Close):
    [tag:1][off:4 LE][font/attr runs ...][0][T.len:4][chars at file offset `off`].
    `off` is the absolute file offset where the characters begin.
    """
    if len(data) < 5:
        return data
    off = int.from_bytes(data[1:5], "little", signed=True)
    if 5 <= off <= len(data):
        return data[off:]
    return data
