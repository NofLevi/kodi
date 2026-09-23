"""AES-128/192/256 in CBC mode, encryption only, in the standard library.

Here for one reason: Ktuvit authenticates with a value its own JavaScript
computes, and the middle of that computation is AES-CBC. Kodi 21 ships Python
3.8, whose standard library has `hashlib.pbkdf2_hmac` and `sha256` but no
block cipher at all, and `pycryptodome` is a compiled dependency that could
never sit inside a `<platform>all</platform>` zip. So it is written out, the
same way `qr.py` is rather than pulling in `qrcode` and Pillow.

Encryption only, because signing in is the only thing this project does with
a cipher. Decryption would be another sixty lines protecting nothing.

It is checked against the published vectors rather than against itself - the
FIPS-197 worked example and NIST SP 800-38A's CBC case - because a cipher
that is subtly wrong produces bytes that look exactly like a cipher that is
right, and the only symptom is a login that fails with no reason given.
"""

BLOCK = 16


def _build_sbox():
    """The S-box, generated rather than written out.

    256 magic numbers in a source file are 256 chances to typo one, and a
    single wrong entry still encrypts - just to the wrong answer. This is the
    standard generator over GF(2**8), and the FIPS vector in the tests is what
    proves it came out right.
    """
    sbox = [0] * 256
    p = q = 1
    while True:
        # p *= 3
        p = (p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        # q /= 3
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        q &= 0xFF
        affine = (q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6))
                  ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))) & 0xFF
        sbox[p] = affine ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return tuple(sbox)


SBOX = _build_sbox()
RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
        0x6C, 0xD8, 0xAB, 0x4D)


def _xtime(value):
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def _mul(a, b):
    """Multiply in GF(2**8), for the MixColumns constants 2 and 3."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _expand_key(key):
    """The round keys, as 4-byte words. Returns them with the round count."""
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError("a key must be 16, 24 or 32 bytes")
    rounds = nk + 6
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (rounds + 1)):
        word = list(words[i - 1])
        if i % nk == 0:
            word = word[1:] + word[:1]                    # RotWord
            word = [SBOX[b] for b in word]                 # SubWord
            word[0] ^= RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            word = [SBOX[b] for b in word]
        words.append([words[i - nk][j] ^ word[j] for j in range(4)])
    return words, rounds


def _encrypt_block(block, words, rounds):
    """One 16-byte block. The state is column-major, as the standard has it."""
    state = list(block)
    for column in range(4):
        for row in range(4):
            state[4 * column + row] ^= words[column][row]

    for rnd in range(1, rounds + 1):
        state = [SBOX[b] for b in state]

        shifted = list(state)                              # ShiftRows
        for row in range(1, 4):
            cells = [state[4 * column + row] for column in range(4)]
            cells = cells[row:] + cells[:row]
            for column in range(4):
                shifted[4 * column + row] = cells[column]
        state = shifted

        if rnd != rounds:                                  # MixColumns
            mixed = []
            for column in range(4):
                a = state[4 * column:4 * column + 4]
                mixed.extend([
                    _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3],
                    a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3],
                    a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3),
                    _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2),
                ])
            state = mixed

        for column in range(4):
            for row in range(4):
                state[4 * column + row] ^= words[rnd * 4 + column][row]

    return bytes(bytearray(state))


def pad(data):
    """PKCS#7. A full block gains a whole block, which is the point of it."""
    short = BLOCK - (len(data) % BLOCK)
    return data + bytes(bytearray([short] * short))


def encrypt_cbc(data, key, iv, padding=True):
    """Encrypt with CBC chaining, PKCS#7 padded unless told otherwise."""
    if len(iv) != BLOCK:
        raise ValueError("the initialisation vector must be 16 bytes")
    if padding:
        data = pad(data)
    if len(data) % BLOCK:
        raise ValueError("unpadded input must be a multiple of 16 bytes")

    words, rounds = _expand_key(key)
    previous = bytearray(iv)
    out = bytearray()
    for start in range(0, len(data), BLOCK):
        block = bytearray(data[start:start + BLOCK])
        for i in range(BLOCK):
            block[i] ^= previous[i]
        encrypted = _encrypt_block(bytes(block), words, rounds)
        out.extend(encrypted)
        previous = bytearray(encrypted)
    return bytes(out)
