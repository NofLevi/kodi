"""AES, against the published vectors rather than against itself.

The same argument as `test_qr.py`: a cipher that is subtly wrong produces
bytes indistinguishable from a cipher that is right, and the only symptom is
a login that fails with no reason given. So the S-box, the key schedule and
the chaining are all checked against values somebody else published - FIPS-197
for the block cipher and NIST SP 800-38A for CBC - and none of the expected
values here were produced by this code.
"""
import binascii

import pytest

from pinky.utils import aes


def hexb(text):
    return binascii.unhexlify(text.replace(" ", ""))


# --------------------------------------------------------------------------
# the block cipher, FIPS-197 appendix C
# --------------------------------------------------------------------------

PLAIN = "00112233445566778899aabbccddeeff"


@pytest.mark.parametrize("key,expected", [
    ("000102030405060708090a0b0c0d0e0f",
     "69c4e0d86a7b0430d8cdb78070b4c55a"),                       # AES-128
    ("000102030405060708090a0b0c0d0e0f1011121314151617",
     "dda97ca4864cdfe06eaf70a0ec0d7191"),                       # AES-192
    ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
     "8ea2b7ca516745bfeafc49904b496089"),                       # AES-256
])
def test_a_single_block_matches_the_standards_worked_example(key, expected):
    encrypted = aes.encrypt_cbc(hexb(PLAIN), hexb(key), b"\x00" * 16,
                                padding=False)
    assert binascii.hexlify(encrypted).decode() == expected


def test_the_sbox_is_a_permutation():
    """Generated rather than written out, so this is the cheap sanity check:
    256 distinct values. A duplicate would mean the generator drifted."""
    assert len(aes.SBOX) == 256
    assert len(set(aes.SBOX)) == 256
    assert aes.SBOX[0] == 0x63, "the standard fixes this one"


# --------------------------------------------------------------------------
# chaining, NIST SP 800-38A section F.2.1
# --------------------------------------------------------------------------


def test_cbc_chains_blocks_the_way_the_specification_says():
    encrypted = aes.encrypt_cbc(
        hexb("6bc1bee22e409f96e93d7e117393172a"
             "ae2d8a571e03ac9c9eb76fac45af8e51"
             "30c81c46a35ce411e5fbc1191a0a52ef"
             "f69f2445df4f9b17ad2b417be66c3710"),
        hexb("2b7e151628aed2a6abf7158809cf4f3c"),
        hexb("000102030405060708090a0b0c0d0e0f"),
        padding=False)
    assert binascii.hexlify(encrypted).decode() == (
        "7649abac8119b246cee98e9b12e9197d"
        "5086cb9b507219ee95db113a917678b2"
        "73bed6b8e3c1743b7116e69e22229516"
        "3ff1caa1681fac09120eca307586e1a7")


def test_the_same_block_twice_encrypts_differently():
    """Which is the whole point of chaining, and the one thing a wrong IV or
    a dropped XOR would quietly take away."""
    encrypted = aes.encrypt_cbc(hexb(PLAIN + PLAIN),
                                hexb("000102030405060708090a0b0c0d0e0f"),
                                b"\x00" * 16, padding=False)
    assert encrypted[:16] != encrypted[16:]


# --------------------------------------------------------------------------
# padding and refusals
# --------------------------------------------------------------------------


def test_padding_always_adds_something():
    """PKCS#7 gives a whole block to input that already fits, so that the
    length is never ambiguous."""
    assert aes.pad(b"") == b"\x10" * 16
    assert aes.pad(b"A" * 16) == b"A" * 16 + b"\x10" * 16
    assert aes.pad(b"A" * 15) == b"A" * 15 + b"\x01"
    assert len(aes.pad(b"A" * 17)) == 32


def test_a_bad_key_or_vector_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        aes.encrypt_cbc(b"x" * 16, b"short", b"\x00" * 16, padding=False)
    with pytest.raises(ValueError):
        aes.encrypt_cbc(b"x" * 16, b"\x00" * 16, b"short", padding=False)
    with pytest.raises(ValueError):
        aes.encrypt_cbc(b"not a whole block", b"\x00" * 16, b"\x00" * 16,
                        padding=False)
