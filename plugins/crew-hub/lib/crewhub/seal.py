"""Sealed command results (docs/COMMANDS.md, `transcript`): HMAC-SHA256 counter-mode keystream, encrypt-then-MAC.

Stdlib only, Python 3.9+. The HA integration implements the same scheme; tests/test_seal.py and
docs/evidence/transcript-seal-vectors.json hold the shared test vectors.

- `S` = the machine's command secret, as UTF-8 bytes (the same key bytes the command signatures use).
- ke = HMAC-SHA256(S, "bridge-transcript-enc-v1"), km = HMAC-SHA256(S, "bridge-transcript-mac-v1").
- keystream block i = HMAC-SHA256(ke, n || i as 8-byte big-endian), i = 0, 1, ...; c = plaintext XOR keystream.
- t = HMAC-SHA256(km, "v1" || n || c). Wire form: {"n": hex(n), "c": base64(c), "t": hex(t)}.
Receivers verify the tag (constant time) before decrypting.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re

NONCE_LEN = 16
ENC_LABEL = b"bridge-transcript-enc-v1"
MAC_LABEL = b"bridge-transcript-mac-v1"
TAG_PREFIX = b"v1"
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


class SealError(Exception):
    """args[0] is the error code ("bad_seal")."""


def _key_bytes(secret):
    if isinstance(secret, str):
        return secret.encode("utf-8")
    if isinstance(secret, (bytes, bytearray)):
        return bytes(secret)
    raise TypeError("secret must be str or bytes")


def keys(secret):
    """(ke, km) for a command secret."""
    s = _key_bytes(secret)
    return hmac.new(s, ENC_LABEL, hashlib.sha256).digest(), hmac.new(s, MAC_LABEL, hashlib.sha256).digest()


def _xor_stream(ke, nonce, data):
    out = bytearray(len(data))
    for i in range(0, len(data), 32):
        block = hmac.new(ke, nonce + (i // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        for j, b in enumerate(chunk):
            out[i + j] = b ^ block[j]
    return bytes(out)


def _tag(km, nonce, cipher):
    return hmac.new(km, TAG_PREFIX + nonce + cipher, hashlib.sha256).digest()


def seal_bytes(secret, plaintext, nonce=None):
    """{"n", "c", "t"} for raw plaintext bytes. `nonce` (16 bytes) only for test vectors; normally os.urandom(16)."""
    if nonce is None:
        nonce = os.urandom(NONCE_LEN)
    if not isinstance(nonce, (bytes, bytearray)) or len(nonce) != NONCE_LEN:
        raise ValueError("nonce must be 16 bytes")
    nonce = bytes(nonce)
    ke, km = keys(secret)
    cipher = _xor_stream(ke, nonce, bytes(plaintext))
    return {"n": binascii.hexlify(nonce).decode("ascii"), "c": base64.b64encode(cipher).decode("ascii"),
            "t": binascii.hexlify(_tag(km, nonce, cipher)).decode("ascii")}


def encode(obj):
    """The plaintext bytes for a JSON object: compact UTF-8 JSON."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def seal(secret, obj, nonce=None):
    """{"sealed": {"n", "c", "t"}} for a JSON-serialisable object (bytes are taken as the plaintext as is)."""
    data = bytes(obj) if isinstance(obj, (bytes, bytearray)) else encode(obj)
    return {"sealed": seal_bytes(secret, data, nonce)}


def open_bytes(secret, sealed):
    """Plaintext bytes of {"n", "c", "t"} (or {"sealed": {...}}); SealError("bad_seal") on any defect."""
    if isinstance(sealed, dict) and isinstance(sealed.get("sealed"), dict):
        sealed = sealed["sealed"]
    if not isinstance(sealed, dict):
        raise SealError("bad_seal")
    n, c, t = sealed.get("n"), sealed.get("c"), sealed.get("t")
    if not (isinstance(n, str) and _HEX32.match(n) and isinstance(t, str) and _HEX64.match(t) and isinstance(c, str)):
        raise SealError("bad_seal")
    try:
        nonce = binascii.unhexlify(n)
        tag = binascii.unhexlify(t)
        cipher = base64.b64decode(c.encode("ascii"), validate=True)
    except (ValueError, binascii.Error, UnicodeEncodeError):
        raise SealError("bad_seal")
    ke, km = keys(secret)
    if not hmac.compare_digest(_tag(km, nonce, cipher), tag):
        raise SealError("bad_seal")
    return _xor_stream(ke, nonce, cipher)


def open_sealed(secret, sealed):
    """The object inside a seal (verify first, then decrypt and parse)."""
    data = open_bytes(secret, sealed)
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise SealError("bad_seal")
