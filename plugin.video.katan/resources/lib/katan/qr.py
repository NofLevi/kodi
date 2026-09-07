"""A QR encoder, and a PNG writer, in the standard library alone.

Signing in to a debrid service on a television is the worst input problem this
add-on has: a thirty-two character key typed on an on-screen keyboard with a
remote. Every service offers a better way - open a short URL on your phone and
type a six character code - and a QR code removes the last of the typing.

Why this is written here rather than fetched or vendored. Every online QR
service would be handed the authorisation URL, which is a live credential for
the few minutes it lasts, and the whole point of this project is that it has
no dependencies at all: `qrcode` needs Pillow, and Pillow is not something to
put on a projector with a gigabyte of RAM.

Scope is deliberately narrow. Byte mode only, versions one to ten, which is up
to 271 characters - several times any device-authorisation URL. Anything
longer raises rather than silently producing a code that will not scan.

The tables are from ISO/IEC 18004. Two of them are checked against arithmetic
in the test suite rather than trusted: the block table has to add up to each
version's total codeword count, and the format strings have to match the
published list. A typo in either produces a QR code that looks perfectly
plausible and does not scan, which is not a thing anybody would debug from a
photograph.
"""
import io
import os
import struct
import zlib

from . import kodi

# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

# Total codewords per version, which is what the block table must add up to.
TOTAL_CODEWORDS = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
                   6: 172, 7: 196, 8: 242, 9: 292, 10: 346}

# (error correction codewords per block, [(block count, data codewords), ...])
BLOCKS = {
    (1, "L"): (7, [(1, 19)]),
    (1, "M"): (10, [(1, 16)]),
    (1, "Q"): (13, [(1, 13)]),
    (1, "H"): (17, [(1, 9)]),
    (2, "L"): (10, [(1, 34)]),
    (2, "M"): (16, [(1, 28)]),
    (2, "Q"): (22, [(1, 22)]),
    (2, "H"): (28, [(1, 16)]),
    (3, "L"): (15, [(1, 55)]),
    (3, "M"): (26, [(1, 44)]),
    (3, "Q"): (18, [(2, 17)]),
    (3, "H"): (22, [(2, 13)]),
    (4, "L"): (20, [(1, 80)]),
    (4, "M"): (18, [(2, 32)]),
    (4, "Q"): (26, [(2, 24)]),
    (4, "H"): (16, [(4, 9)]),
    (5, "L"): (26, [(1, 108)]),
    (5, "M"): (24, [(2, 43)]),
    (5, "Q"): (18, [(2, 15), (2, 16)]),
    (5, "H"): (22, [(2, 11), (2, 12)]),
    (6, "L"): (18, [(2, 68)]),
    (6, "M"): (16, [(4, 27)]),
    (6, "Q"): (24, [(4, 19)]),
    (6, "H"): (28, [(4, 15)]),
    (7, "L"): (20, [(2, 78)]),
    (7, "M"): (18, [(4, 31)]),
    (7, "Q"): (18, [(2, 14), (4, 15)]),
    (7, "H"): (26, [(4, 13), (1, 14)]),
    (8, "L"): (24, [(2, 97)]),
    (8, "M"): (22, [(2, 38), (2, 39)]),
    (8, "Q"): (22, [(4, 18), (2, 19)]),
    (8, "H"): (26, [(4, 14), (2, 15)]),
    (9, "L"): (30, [(2, 116)]),
    (9, "M"): (22, [(3, 36), (2, 37)]),
    (9, "Q"): (20, [(4, 16), (4, 17)]),
    (9, "H"): (24, [(4, 12), (4, 13)]),
    (10, "L"): (18, [(2, 68), (2, 69)]),
    (10, "M"): (26, [(4, 43), (1, 44)]),
    (10, "Q"): (24, [(6, 19), (2, 20)]),
    (10, "H"): (28, [(6, 15), (2, 16)]),
}

# Row and column centres of the alignment patterns.
ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}

EC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
BYTE_MODE = 0b0100

MAX_VERSION = 10


class TooMuchData(ValueError):
    """The text does not fit in a version this module supports."""


# --------------------------------------------------------------------------
# GF(256), for the Reed-Solomon error correction
# --------------------------------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables():
    value = 1
    for power in range(255):
        _EXP[power] = value
        _LOG[value] = power
        value <<= 1
        if value & 0x100:            # the primitive polynomial, 0x11D
            value ^= 0x11D
    for power in range(255, 512):
        _EXP[power] = _EXP[power - 255]


_build_tables()


def _multiply(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    """The generator polynomial for `degree` error correction codewords."""
    poly = [1]
    for power in range(degree):
        nxt = [0] * (len(poly) + 1)
        for index, coefficient in enumerate(poly):
            nxt[index] ^= coefficient
            nxt[index + 1] ^= _multiply(coefficient, _EXP[power])
        poly = nxt
    return poly


def error_codewords(data, count):
    """Reed-Solomon check bytes for one block."""
    poly = _generator(count)
    remainder = list(data) + [0] * count
    for index in range(len(data)):
        factor = remainder[index]
        if factor == 0:
            continue
        for offset, coefficient in enumerate(poly):
            remainder[index + offset] ^= _multiply(coefficient, factor)
    return remainder[len(data):]


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------


def _capacity(version, level):
    _ec, groups = BLOCKS[(version, level)]
    return sum(count * data for count, data in groups)


def _pick_version(length, level):
    for version in range(1, MAX_VERSION + 1):
        header = 4 + (8 if version < 10 else 16)
        if (header + length * 8) <= _capacity(version, level) * 8:
            return version
    raise TooMuchData(
        "%d characters will not fit in a version %d QR code"
        % (length, MAX_VERSION))


def _bitstream(data, version, level):
    """Mode, length, payload, terminator, padding - as a list of bits."""
    bits = []

    def push(value, width):
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(BYTE_MODE, 4)
    push(len(data), 8 if version < 10 else 16)
    for byte in data:
        push(byte, 8)

    capacity = _capacity(version, level) * 8
    # Up to four zero bits of terminator, then round up to a whole byte.
    bits.extend([0] * min(4, capacity - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    # The two alternating pad bytes the specification names.
    for pad in _cycle((0xEC, 0x11)):
        if len(bits) >= capacity:
            break
        push(pad, 8)
    return bits


def _cycle(values):
    while True:
        for value in values:
            yield value


def _codewords(bits):
    out = []
    for index in range(0, len(bits), 8):
        byte = 0
        for bit in bits[index:index + 8]:
            byte = (byte << 1) | bit
        out.append(byte)
    return out


def _interleave(codewords, version, level):
    """Split into blocks, add check bytes, then interleave both."""
    ec_count, groups = BLOCKS[(version, level)]

    blocks = []
    position = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(codewords[position:position + size])
            position += size

    checks = [error_codewords(block, ec_count) for block in blocks]

    out = []
    for index in range(max(len(block) for block in blocks)):
        for block in blocks:
            if index < len(block):
                out.append(block[index])
    for index in range(ec_count):
        for block in checks:
            out.append(block[index])
    return out


# --------------------------------------------------------------------------
# the matrix
# --------------------------------------------------------------------------


def _size(version):
    return version * 4 + 17


def _blank(version):
    size = _size(version)
    return ([[None] * size for _ in range(size)],
            [[False] * size for _ in range(size)])


def _place_finder(matrix, reserved, top, left):
    size = len(matrix)
    for row in range(-1, 8):
        for column in range(-1, 8):
            y, x = top + row, left + column
            if not (0 <= y < size and 0 <= x < size):
                continue
            inside = (0 <= row < 7 and 0 <= column < 7)
            dark = inside and (
                row in (0, 6) or column in (0, 6)
                or (2 <= row <= 4 and 2 <= column <= 4))
            matrix[y][x] = 1 if dark else 0
            reserved[y][x] = True


def _place_alignment(matrix, reserved, version):
    centres = ALIGNMENT[version]
    size = len(matrix)
    for row in centres:
        for column in centres:
            # Not over the three finder patterns.
            if (row, column) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    dark = max(abs(dy), abs(dx)) != 1
                    matrix[row + dy][column + dx] = 1 if dark else 0
                    reserved[row + dy][column + dx] = True


def _place_timing(matrix, reserved):
    size = len(matrix)
    for position in range(8, size - 8):
        bit = 1 if position % 2 == 0 else 0
        matrix[6][position] = bit
        reserved[6][position] = True
        matrix[position][6] = bit
        reserved[position][6] = True


def _reserve_format(matrix, reserved, version):
    size = len(matrix)
    for index in range(9):
        if not reserved[8][index]:
            reserved[8][index] = True
            matrix[8][index] = 0
        if not reserved[index][8]:
            reserved[index][8] = True
            matrix[index][8] = 0
    for index in range(8):
        reserved[8][size - 1 - index] = True
        matrix[8][size - 1 - index] = 0
        reserved[size - 1 - index][8] = True
        matrix[size - 1 - index][8] = 0
    # The one module that is always dark.
    matrix[size - 8][8] = 1
    reserved[size - 8][8] = True

    if version >= 7:
        for index in range(18):
            row, column = index // 3, index % 3
            matrix[row][size - 11 + column] = 0
            reserved[row][size - 11 + column] = True
            matrix[size - 11 + column][row] = 0
            reserved[size - 11 + column][row] = True


def _place_data(matrix, reserved, codewords):
    """The zigzag walk, two columns at a time, from the bottom right."""
    size = len(matrix)
    bits = []
    for byte in codewords:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)

    index = 0
    upward = True
    column = size - 1
    while column > 0:
        if column == 6:               # the vertical timing pattern
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                x = column - offset
                if reserved[row][x]:
                    continue
                matrix[row][x] = bits[index] if index < len(bits) else 0
                index += 1
        upward = not upward
        column -= 2
    return matrix


MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _apply_mask(matrix, reserved, pattern):
    size = len(matrix)
    out = [row[:] for row in matrix]
    rule = MASKS[pattern]
    for row in range(size):
        for column in range(size):
            if not reserved[row][column] and rule(row, column):
                out[row][column] ^= 1
    return out


def format_bits(level, mask):
    """The fifteen bit format string: five data bits, BCH coded, then masked.

    The mask at the end is not optional decoration - it is what stops the
    all-zero format (level M, mask 0) becoming fifteen zero modules, which no
    scanner could tell from blank quiet zone. The test suite checks all
    thirty-two of these against the published table.
    """
    value = (EC_BITS[level] << 3) | mask
    remainder = value << 10
    for shift in range(14, 9, -1):
        if (remainder >> shift) & 1:
            remainder ^= 0b10100110111 << (shift - 10)
    return ((value << 10) | remainder) ^ 0b101010000010010


def version_bits(version):
    """The eighteen bit version string, for versions seven and up."""
    remainder = version << 12
    for shift in range(17, 11, -1):
        if remainder >> shift & 1:
            remainder ^= 0b1111100100101 << (shift - 12)
    return (version << 12) | remainder


def _write_format(matrix, level, mask):
    size = len(matrix)
    bits = format_bits(level, mask)
    for index in range(15):
        bit = (bits >> index) & 1
        # The copy beside the top-left finder.
        if index < 6:
            matrix[8][index] = bit
        elif index == 6:
            matrix[8][7] = bit
        elif index == 7:
            matrix[8][8] = bit
        elif index == 8:
            matrix[7][8] = bit
        else:
            matrix[14 - index][8] = bit
        # And the split copy beside the other two.
        if index < 8:
            matrix[size - 1 - index][8] = bit
        else:
            matrix[8][size - 15 + index] = bit


def _write_version(matrix, version):
    if version < 7:
        return
    size = len(matrix)
    bits = version_bits(version)
    for index in range(18):
        bit = (bits >> index) & 1
        row, column = index // 3, index % 3
        matrix[row][size - 11 + column] = bit
        matrix[size - 11 + column][row] = bit


# --------------------------------------------------------------------------
# penalties, which decide the mask
# --------------------------------------------------------------------------


def _penalty(matrix):
    size = len(matrix)
    score = 0

    # Rule 1: runs of five or more of the same colour.
    for line in list(matrix) + [list(column) for column in zip(*matrix)]:
        run, previous = 1, line[0]
        for value in line[1:]:
            if value == previous:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, previous = 1, value
        if run >= 5:
            score += 3 + (run - 5)

    # Rule 2: two by two blocks of one colour.
    for row in range(size - 1):
        for column in range(size - 1):
            block = (matrix[row][column], matrix[row][column + 1],
                     matrix[row + 1][column], matrix[row + 1][column + 1])
            if block[0] == block[1] == block[2] == block[3]:
                score += 3

    # Rule 3: the finder-like pattern, either way round.
    wanted = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    backwards = wanted[::-1]
    for line in list(matrix) + [list(column) for column in zip(*matrix)]:
        for start in range(size - 10):
            window = line[start:start + 11]
            if window == wanted or window == backwards:
                score += 40

    # Rule 4: how far the dark proportion is from a half.
    dark = sum(sum(row) for row in matrix)
    percent = dark * 100 // (size * size)
    score += 10 * min(abs(percent - 50) // 5, abs(percent - 50 + 4) // 5)
    return score


# --------------------------------------------------------------------------
# the public part
# --------------------------------------------------------------------------


def encode(text, level="M"):
    """Return the QR matrix for `text` as rows of 0 and 1."""
    if isinstance(text, bytes):
        data = text
    else:
        data = text.encode("utf-8")
    version = _pick_version(len(data), level)

    codewords = _interleave(
        _codewords(_bitstream(data, version, level)), version, level)

    matrix, reserved = _blank(version)
    size = _size(version)
    _place_finder(matrix, reserved, 0, 0)
    _place_finder(matrix, reserved, 0, size - 7)
    _place_finder(matrix, reserved, size - 7, 0)
    _place_alignment(matrix, reserved, version)
    _place_timing(matrix, reserved)
    _reserve_format(matrix, reserved, version)
    _place_data(matrix, reserved, codewords)

    best, best_score = None, None
    for mask in range(8):
        candidate = _apply_mask(matrix, reserved, mask)
        _write_format(candidate, level, mask)
        _write_version(candidate, version)
        score = _penalty(candidate)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


def png_bytes(matrix, scale=8, quiet=4):
    """A black and white PNG of the matrix, as bytes.

    Written by hand because Pillow is not a dependency this add-on will take
    on. Greyscale, one byte per pixel, which is larger on disk than a bit
    depth of one and very much simpler to be sure of.
    """
    size = len(matrix)
    width = (size + quiet * 2) * scale

    rows = []
    blank = b"\xff" * width
    for _ in range(quiet * scale):
        rows.append(blank)
    for line in matrix:
        pixels = bytearray()
        pixels.extend(b"\xff" * (quiet * scale))
        for value in line:
            pixels.extend((b"\x00" if value else b"\xff") * scale)
        pixels.extend(b"\xff" * (quiet * scale))
        for _ in range(scale):
            rows.append(bytes(pixels))
    for _ in range(quiet * scale):
        rows.append(blank)

    raw = b"".join(b"\x00" + row for row in rows)      # filter type 0 per row

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    out = io.BytesIO()
    out.write(b"\x89PNG\r\n\x1a\n")
    out.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, len(rows),
                                         8, 0, 0, 0, 0)))
    out.write(chunk(b"IDAT", zlib.compress(raw, 9)))
    out.write(chunk(b"IEND", b""))
    return out.getvalue()


def write(text, path, scale=8, level="M"):
    """Write a QR code for `text` to `path`. Returns the path, or "".

    Never raises. A QR code is an extra way to do something there is already
    a way to do, so a failure here must not take the sign-in flow with it.
    """
    try:
        data = png_bytes(encode(text, level), scale=scale)
    except Exception:
        kodi.log_exception("could not build a QR code")
        return ""
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(path, "wb") as handle:
            handle.write(data)
    except (IOError, OSError):
        kodi.log_exception("could not write the QR code to %s" % path)
        return ""
    return path


def image_for(text, name="auth"):
    """A QR code written into the profile directory, ready for a control.

    The name alternates nothing and the file is overwritten: Kodi caches
    textures by path, so a second code written to the same name would show the
    first one. Callers pass a name per purpose, and `bust` handles the rest.
    """
    folder = os.path.join(kodi.profile_path(), "qr")
    return write(text, os.path.join(folder, "%s-%s.png" % (name, _bust(text))))


def _bust(text):
    """A short stable tag for this text, so a new code is a new file.

    Kodi caches a texture against its path for the life of the session, so
    writing a different QR code to the same filename shows the previous one -
    which during a sign-in flow means a code for an expired request.
    """
    return "%08x" % (zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF)


def cleanup(keep=4):
    """Drop old QR files, oldest first. They are a few kilobytes each."""
    folder = os.path.join(kodi.profile_path(), "qr")
    try:
        names = [os.path.join(folder, name) for name in os.listdir(folder)
                 if name.endswith(".png")]
    except (IOError, OSError):
        return 0
    names.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    removed = 0
    for path in names[keep:]:
        try:
            os.remove(path)
            removed += 1
        except (IOError, OSError):
            pass
    return removed
