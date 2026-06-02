import struct

from puck.text import TEXT_TAG, from_oberon, to_oberon


def test_lf_to_cr():
    assert to_oberon("a\nb\n") == b"a\rb\r"


def test_crlf_normalized_to_cr():
    assert to_oberon("a\r\nb") == b"a\rb"


def test_cr_to_lf():
    assert from_oberon(b"a\rb\r") == "a\nb\n"


def test_plain_ascii_roundtrip():
    s = "MODULE M;\n  x := 1\nEND M.\n"
    assert from_oberon(to_oberon(s)) == s


def test_strip_formatted_header():
    chars = b"MODULE X;\rEND X.\r"
    filler = b"\x01\x02\x03\x04\x05"  # stand-in for the font/attr run header
    off = 5 + len(filler)
    data = bytes([TEXT_TAG]) + struct.pack("<i", off) + filler + chars
    assert from_oberon(data) == "MODULE X;\nEND X.\n"


def test_plain_file_not_mistaken_for_formatted():
    # first byte 'M' (0x4D), not 0xF1 -> treated as ASCII
    assert from_oberon(b"MODULE M;\r") == "MODULE M;\n"


def test_from_oberon_empty_bytes():
    """`data[:1]` is b'' for empty input, falls through to decode, returns ''."""
    assert from_oberon(b"") == ""


def test_from_oberon_lone_text_tag_is_too_short_for_header():
    """`_strip_text_header` bails on len(data) < 5; the byte falls through to latin1 decode.
    0xF1 -> 'ñ' under latin1 — what matters is no crash."""
    assert from_oberon(bytes([TEXT_TAG])) == "ñ"


def test_from_oberon_with_offset_out_of_range_returns_unchanged():
    """An offset past EOF means the header is unparseable; we return data unchanged
    rather than indexing into garbage."""
    data = bytes([TEXT_TAG]) + struct.pack("<i", 100) + b"abc"
    out = from_oberon(data)
    # First byte decoded as latin1 'ñ' (so we know we didn't strip), then the offset
    # bytes decode to a few latin1 chars, then 'abc'. We just assert it didn't crash
    # and contains the trailing characters.
    assert out.endswith("abc")
    assert out.startswith("ñ")


def test_from_oberon_with_offset_at_minimum_5():
    """offset = 5 is the minimum valid value (header is the 5-byte tag+offset only)."""
    chars = b"MODULE X;\r"
    data = bytes([TEXT_TAG]) + struct.pack("<i", 5) + chars
    assert from_oberon(data) == "MODULE X;\n"


def test_to_oberon_replaces_non_latin1_chars():
    """`encode(latin1, errors='replace')` swaps unrepresentable chars for '?'."""
    assert to_oberon("α") == b"?"
    assert to_oberon("hi 🦆 ya\n") == b"hi ? ya\r"


def test_to_oberon_keeps_latin1_supplement_chars():
    """Latin-1 supplement (U+0080..U+00FF) round-trips through encode/decode."""
    assert to_oberon("ñ") == b"\xf1"
    assert from_oberon(b"\xf1") == "ñ"
