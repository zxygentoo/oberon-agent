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
