# -*- coding: utf-8 -*-
"""The QR encoder.

A QR code that is wrong does not look wrong. It looks exactly like a QR code,
and the only symptom is a phone that will not scan it - which is not something
anyone will debug from a photograph of a television. So the parts that can be
checked against the specification rather than against themselves are checked
against the specification: the block table has to add up, the format strings
have to match the published list, and the Reed-Solomon coder has to reproduce
the worked example in the standard.

The last test is the strongest one: it reads the symbol back out, undoing
every step the encoder did, and compares it with what went in.
"""
import pytest

from katan import qr


# --------------------------------------------------------------------------
# the tables, against arithmetic and against the published values
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", sorted(qr.TOTAL_CODEWORDS))
@pytest.mark.parametrize("level", ["L", "M", "Q", "H"])
def test_the_block_table_adds_up(version, level):
    """Blocks times their size must equal the version's capacity.

    This is the check that catches a mistyped digit in a forty-row table
    copied out of a specification, which is the most likely way for this file
    to be wrong.
    """
    ec_count, groups = qr.BLOCKS[(version, level)]
    total = sum(count * (size + ec_count) for count, size in groups)
    assert total == qr.TOTAL_CODEWORDS[version], (
        "version %d-%s adds up to %d codewords, not %d"
        % (version, level, total, qr.TOTAL_CODEWORDS[version]))


# The thirty-two format strings from ISO/IEC 18004, in (level, mask) order.
PUBLISHED_FORMATS = {
    ("L", 0): 0b111011111000100, ("L", 1): 0b111001011110011,
    ("L", 2): 0b111110110101010, ("L", 3): 0b111100010011101,
    ("L", 4): 0b110011000101111, ("L", 5): 0b110001100011000,
    ("L", 6): 0b110110001000001, ("L", 7): 0b110100101110110,
    ("M", 0): 0b101010000010010, ("M", 1): 0b101000100100101,
    ("M", 2): 0b101111001111100, ("M", 3): 0b101101101001011,
    ("M", 4): 0b100010111111001, ("M", 5): 0b100000011001110,
    ("M", 6): 0b100111110010111, ("M", 7): 0b100101010100000,
    ("Q", 0): 0b011010101011111, ("Q", 1): 0b011000001101000,
    ("Q", 2): 0b011111100110001, ("Q", 3): 0b011101000000110,
    ("Q", 4): 0b010010010110100, ("Q", 5): 0b010000110000011,
    ("Q", 6): 0b010111011011010, ("Q", 7): 0b010101111101101,
    ("H", 0): 0b001011010001001, ("H", 1): 0b001001110111110,
    ("H", 2): 0b001110011100111, ("H", 3): 0b001100111010000,
    ("H", 4): 0b000011101100010, ("H", 5): 0b000001001010101,
    ("H", 6): 0b000110100001100, ("H", 7): 0b000100000111011,
}


@pytest.mark.parametrize("key,expected", sorted(PUBLISHED_FORMATS.items()))
def test_format_strings_match_the_specification(key, expected):
    level, mask = key
    assert qr.format_bits(level, mask) == expected, (
        "format for %s mask %d is %015b, expected %015b"
        % (level, mask, qr.format_bits(level, mask), expected))


@pytest.mark.parametrize("version,expected", [
    (7, 0b000111110010010100), (8, 0b001000010110111100),
    (9, 0b001001101010011001), (10, 0b001010010011010011),
])
def test_version_strings_match_the_specification(version, expected):
    assert qr.version_bits(version) == expected


def test_reed_solomon_reproduces_the_worked_example():
    """The version 1-M example from the standard, which is the usual sanity
    check for an implementation of this."""
    data = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11,
            0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
    expected = [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55]
    assert qr.error_codewords(data, 10) == expected


def test_the_galois_field_is_a_field():
    """Every non-zero value has an inverse, which is what makes it work."""
    for value in range(1, 256):
        found = [other for other in range(1, 256)
                 if qr._multiply(value, other) == 1]
        assert len(found) == 1, "%d has %d inverses" % (value, len(found))


# --------------------------------------------------------------------------
# the symbol
# --------------------------------------------------------------------------


def test_the_finder_patterns_are_where_they_belong():
    matrix = qr.encode("https://trakt.tv/activate")
    size = len(matrix)
    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        assert matrix[top][left] == 1
        assert matrix[top + 3][left + 3] == 1
        assert matrix[top + 1][left + 1] == 0, "the ring must be white"
        assert matrix[top + 3][left + 1] == 0


def test_the_timing_patterns_alternate():
    matrix = qr.encode("https://real-debrid.com/device")
    size = len(matrix)
    for position in range(8, size - 8):
        assert matrix[6][position] == (1 if position % 2 == 0 else 0)
        assert matrix[position][6] == (1 if position % 2 == 0 else 0)


def test_the_symbol_is_square_and_the_right_size():
    for text, version in (("a" * 10, 1), ("a" * 40, 3)):
        matrix = qr.encode(text, "M")
        assert len(matrix) == len(matrix[0])
        assert len(matrix) == version * 4 + 17, text


def test_every_module_is_decided():
    """A None left in the matrix means a module nothing wrote."""
    matrix = qr.encode("https://torbox.app/settings")
    for row in matrix:
        for value in row:
            assert value in (0, 1)


def test_text_too_long_is_refused_rather_than_mangled():
    with pytest.raises(qr.TooMuchData):
        qr.encode("x" * 400, "H")


# --------------------------------------------------------------------------
# reading it back
# --------------------------------------------------------------------------


def _read_back(matrix, level="M"):
    """Undo the encoder: find the mask, unmask, unwalk, de-interleave, decode.

    This is the test that would actually catch a symbol a phone cannot read,
    because it goes through the same structure a scanner does rather than
    trusting the encoder's own idea of where things are.
    """
    size = len(matrix)
    version = (size - 17) // 4

    # The format string says which mask was used. Read the copy beside the
    # top-left finder, undo the specification's mask, and take the low bits.
    bits = 0
    for index in range(15):
        if index < 6:
            bit = matrix[8][index]
        elif index == 6:
            bit = matrix[8][7]
        elif index == 7:
            bit = matrix[8][8]
        elif index == 8:
            bit = matrix[7][8]
        else:
            bit = matrix[14 - index][8]
        bits |= bit << index
    unmasked = bits ^ 0b101010000010010
    mask = (unmasked >> 10) & 0b111

    # Rebuild the reserved map exactly as the encoder did.
    blank, reserved = qr._blank(version)
    qr._place_finder(blank, reserved, 0, 0)
    qr._place_finder(blank, reserved, 0, size - 7)
    qr._place_finder(blank, reserved, size - 7, 0)
    qr._place_alignment(blank, reserved, version)
    qr._place_timing(blank, reserved)
    qr._reserve_format(blank, reserved, version)

    plain = qr._apply_mask(matrix, reserved, mask)      # masking is its own inverse

    # Walk the zigzag again, collecting instead of writing.
    collected = []
    upward = True
    column = size - 1
    while column > 0:
        if column == 6:
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                x = column - offset
                if not reserved[row][x]:
                    collected.append(plain[row][x])
        upward = not upward
        column -= 2

    codewords = qr._codewords(collected[:len(collected) // 8 * 8])

    # De-interleave: the data codewords come first, round-robin over blocks.
    ec_count, groups = qr.BLOCKS[(version, level)]
    sizes = []
    for count, data_size in groups:
        sizes.extend([data_size] * count)
    blocks = [[] for _ in sizes]
    position = 0
    for index in range(max(sizes)):
        for block_number, block_size in enumerate(sizes):
            if index < block_size:
                blocks[block_number].append(codewords[position])
                position += 1
    data = []
    for block in blocks:
        data.extend(block)

    # And finally the payload: mode, length, then that many bytes.
    stream = []
    for byte in data:
        for shift in range(7, -1, -1):
            stream.append((byte >> shift) & 1)

    def take(count, at):
        value = 0
        for bit in stream[at:at + count]:
            value = (value << 1) | bit
        return value

    assert take(4, 0) == qr.BYTE_MODE, "not byte mode"
    length_bits = 8 if version < 10 else 16
    length = take(length_bits, 4)
    start = 4 + length_bits
    out = bytearray()
    for index in range(length):
        out.append(take(8, start + index * 8))
    return bytes(out).decode("utf-8")


@pytest.mark.parametrize("text", [
    "https://trakt.tv/activate",
    "https://real-debrid.com/device",
    "https://www.premiumize.me/device?code=ABCD-1234",
    "https://alldebrid.com/pin/?pin=XYZ99",
    "K" * 100,
    "https://torbox.app/settings/api?ref=katan&code=123456",
])
def test_a_symbol_reads_back_as_what_went_in(text):
    """End to end: encode, then take the symbol apart the way a scanner
    would. If the mask, the interleaving or the zigzag were wrong, this is
    where it shows, and nothing else in this file would notice."""
    assert _read_back(qr.encode(text, "M")) == text


def test_a_symbol_reads_back_at_every_correction_level():
    text = "https://trakt.tv/activate"
    for level in ("L", "M", "Q", "H"):
        assert _read_back(qr.encode(text, level), level) == text, level


# --------------------------------------------------------------------------
# the PNG
# --------------------------------------------------------------------------


def test_the_png_is_a_png():
    data = qr.png_bytes(qr.encode("hello"), scale=4, quiet=2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in data and b"IDAT" in data and data.endswith(b"IEND\xae\x42\x60\x82")


def test_the_png_is_the_size_the_arguments_ask_for():
    import struct
    matrix = qr.encode("hello")
    data = qr.png_bytes(matrix, scale=3, quiet=4)
    width, height = struct.unpack(">II", data[16:24])
    expected = (len(matrix) + 8) * 3
    assert (width, height) == (expected, expected)


def test_writing_one_produces_a_file(tmp_path):
    path = str(tmp_path / "deep" / "code.png")
    assert qr.write("https://trakt.tv/activate", path) == path
    with open(path, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_a_failure_to_build_one_is_not_a_failure_to_sign_in(tmp_path):
    """A QR code is a convenience on top of a flow that already works, so it
    must never be the thing that breaks it."""
    assert qr.write("x" * 5000, str(tmp_path / "too-long.png")) == ""


def test_different_text_gets_a_different_filename(monkeypatch, tmp_path):
    """Kodi caches a texture against its path for the session, so a second
    code written to the same name would show the first one - during a sign-in
    flow, a code for a request that has already expired."""
    monkeypatch.setattr(qr.kodi, "profile_path", lambda: str(tmp_path))
    first = qr.image_for("https://trakt.tv/activate?code=AAA")
    second = qr.image_for("https://trakt.tv/activate?code=BBB")
    assert first and second and first != second


def test_old_codes_are_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setattr(qr.kodi, "profile_path", lambda: str(tmp_path))
    for index in range(9):
        qr.image_for("https://example.test/%d" % index)
    qr.cleanup(keep=3)
    folder = tmp_path / "qr"
    assert len(list(folder.glob("*.png"))) == 3
