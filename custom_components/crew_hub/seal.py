"""Sealed command results (docs/COMMANDS.md § transcript): HMAC-CTR + encrypt-then-MAC, stdlib only.

Byte-compatible with the machine side (plugins/crew-hub/lib/crewhub/seal.py):
- ke = HMAC-SHA256(S, "bridge-transcript-enc-v1"), km = HMAC-SHA256(S, "bridge-transcript-mac-v1"), S = the machine's
  command secret as UTF-8, keys = the raw 32-byte digests;
- keystream block i (i = 0, 1, ...) = HMAC-SHA256(ke, n || i as 8-byte big-endian), XORed with the UTF-8 JSON plaintext;
- tag = HMAC-SHA256(km, b"v1" || n || c); wire form {"n": hex 16-byte nonce, "c": base64 ciphertext, "t": hex tag}.
open_sealed checks the tag in constant time before decrypting; every failure is SealError (error code bad_seal).
Never logged: the secret, the keys, the plaintext.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from typing import Any

ENC_LABEL = b"bridge-transcript-enc-v1"
MAC_LABEL = b"bridge-transcript-mac-v1"
TAG_PREFIX = b"v1"
NONCE_LEN = 16
BLOCK = 32  # SHA-256 digest size
MAX_SEALED = 128 * 1024  # ciphertext bytes; the contract caps the plaintext at 48 KB


class SealError(Exception):
    """A sealed result that fails shape, tag or plaintext checks. str() never holds secret material."""


def derive_keys(secret: str | bytes) -> tuple[bytes, bytes]:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return (hmac.new(key, ENC_LABEL, hashlib.sha256).digest(),
            hmac.new(key, MAC_LABEL, hashlib.sha256).digest())


def _xor_stream(ke: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for i in range(0, len(data), BLOCK):
        block = hmac.new(ke, nonce + (i // BLOCK).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + BLOCK]
        out[i:i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, block))
    return bytes(out)


def _tag(km: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(km, TAG_PREFIX + nonce + ciphertext, hashlib.sha256).digest()


def seal(secret: str | bytes, plaintext: Any, nonce: bytes | None = None) -> dict:
    """{"n", "c", "t"} for plaintext (bytes, or a JSON value serialized compactly as UTF-8). nonce: tests only."""
    if isinstance(plaintext, (bytes, bytearray)):
        data = bytes(plaintext)
    else:
        data = json.dumps(plaintext, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    n = os.urandom(NONCE_LEN) if nonce is None else bytes(nonce)
    if len(n) != NONCE_LEN:
        raise ValueError("nonce must be 16 bytes")
    ke, km = derive_keys(secret)
    c = _xor_stream(ke, n, data)
    return {"n": n.hex(), "c": base64.b64encode(c).decode("ascii"), "t": _tag(km, n, c).hex()}


def open_sealed(secret: str | bytes, sealed: Any) -> bytes:
    """The plaintext bytes of a {"n", "c", "t"} object; SealError on any shape or tag failure."""
    if not isinstance(sealed, dict):
        raise SealError("not an object")
    n_hex, c_b64, t_hex = sealed.get("n"), sealed.get("c"), sealed.get("t")
    if not isinstance(n_hex, str) or not isinstance(c_b64, str) or not isinstance(t_hex, str):
        raise SealError("missing field")
    if len(n_hex) != 2 * NONCE_LEN or len(t_hex) != 2 * BLOCK or len(c_b64) > (MAX_SEALED * 4) // 3 + 4:
        raise SealError("bad size")
    try:
        n = bytes.fromhex(n_hex)
        t = bytes.fromhex(t_hex)
        c = base64.b64decode(c_b64.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError) as err:
        raise SealError("bad encoding") from err
    ke, km = derive_keys(secret)
    if not hmac.compare_digest(_tag(km, n, c), t):
        raise SealError("bad tag")
    return _xor_stream(ke, n, c)


def open_json(secret: str | bytes, sealed: Any) -> dict:
    """open_sealed + UTF-8 JSON object decode; SealError when the plaintext isn't a JSON object."""
    raw = open_sealed(secret, sealed)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise SealError("bad plaintext") from err
    if not isinstance(value, dict):
        raise SealError("bad plaintext")
    return value
