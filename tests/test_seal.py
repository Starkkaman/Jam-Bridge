import base64
import hashlib
import hmac
import json
import os
import unittest

import _util
from crewhub import seal

VECTORS = os.path.join(_util.ROOT, "docs", "evidence", "transcript-seal-vectors.json")

# Pinned copy of the first vector: the shared file can't drift without this test noticing.
PINNED = {
    "secret": "bridge-test-secret-0123456789abcdef",
    "nonce_hex": "000102030405060708090a0b0c0d0e0f",
    "plaintext": '{"session":"s1","cursor":"1.0.0","more":false,"turns":[]}',
    "n": "000102030405060708090a0b0c0d0e0f",
    "c": "Llm6pjBrhF8EiV+RB06eDedzwm59Ze8In1p0xAERtVOr00CeaMLLMjIoKxbEkd03g2EfN4D4wWNr",
    "t": "7ca64901a5ec27d54cd96f80c4c35821449a6c28bf149b764c61bd655a2bfe32",
}


def reference(secret, nonce, plaintext):
    """The contract written out independently of crewhub.seal (docs/COMMANDS.md `transcript`)."""
    s = secret.encode("utf-8")
    ke = hmac.new(s, b"bridge-transcript-enc-v1", hashlib.sha256).digest()
    km = hmac.new(s, b"bridge-transcript-mac-v1", hashlib.sha256).digest()
    stream = b""
    i = 0
    while len(stream) < len(plaintext):
        stream += hmac.new(ke, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        i += 1
    c = bytes(a ^ b for a, b in zip(plaintext, stream))
    t = hmac.new(km, b"v1" + nonce + c, hashlib.sha256).hexdigest()
    return {"n": nonce.hex(), "c": base64.b64encode(c).decode("ascii"), "t": t}


class VectorTest(unittest.TestCase):
    def setUp(self):
        with open(VECTORS, "r", encoding="utf-8") as f:
            self.vectors = json.load(f)

    def test_vectors_match_the_contract_and_the_module(self):
        self.assertGreaterEqual(len(self.vectors), 3)
        for v in self.vectors:
            nonce = bytes.fromhex(v["nonce_hex"])
            pt = v["plaintext"].encode("utf-8")
            want = {"n": v["n"], "c": v["c"], "t": v["t"]}
            self.assertEqual(reference(v["secret"], nonce, pt), want)
            self.assertEqual(seal.seal_bytes(v["secret"], pt, nonce), want)
            obj = json.loads(v["plaintext"])
            self.assertEqual(seal.encode(obj), pt)  # plaintext = compact UTF-8 JSON of the object
            self.assertEqual(seal.seal(v["secret"], obj, nonce), {"sealed": want})
            self.assertEqual(seal.seal(v["secret"], pt, nonce), {"sealed": want})  # bytes: the plaintext as is
            self.assertEqual(seal.open_bytes(v["secret"], want), pt)
            self.assertEqual(seal.open_sealed(v["secret"], {"sealed": want}), obj)
            self.assertEqual(seal.open_sealed(v["secret"], want), obj)

    def test_pinned_vector(self):
        v = self.vectors[0]
        self.assertEqual({k: v[k] for k in PINNED}, PINNED)
        self.assertEqual(reference(PINNED["secret"], bytes.fromhex(PINNED["nonce_hex"]), PINNED["plaintext"].encode()),
                         {k: PINNED[k] for k in ("n", "c", "t")})

    def test_multi_block_and_non_ascii(self):
        v = self.vectors[1]
        self.assertGreater(len(v["plaintext"].encode("utf-8")), 3 * 32)
        self.assertNotEqual(len(v["plaintext"].encode("utf-8")), len(v["plaintext"]))  # non-ASCII text


class SealTest(unittest.TestCase):
    SECRET = "s" * 40

    def test_round_trip_random_nonce(self):
        obj = {"session": "s", "turns": [{"kind": "user", "text": "hé " * 500}], "more": False, "cursor": "1.2.3"}
        a = seal.seal(self.SECRET, obj)
        b = seal.seal(self.SECRET, obj)
        self.assertNotEqual(a["sealed"]["n"], b["sealed"]["n"])
        self.assertNotEqual(a["sealed"]["c"], b["sealed"]["c"])
        self.assertEqual(len(a["sealed"]["n"]), 32)
        self.assertEqual(len(a["sealed"]["t"]), 64)
        self.assertEqual(seal.open_sealed(self.SECRET, a), obj)
        self.assertNotIn("hé", json.dumps(a))

    def test_bad_tag_and_tampering(self):
        s = seal.seal(self.SECRET, {"x": 1})["sealed"]
        bad_t = dict(s, t=("0" if s["t"][0] != "0" else "1") + s["t"][1:])
        c = bytearray(base64.b64decode(s["c"]))
        c[0] ^= 1
        bad_c = dict(s, c=base64.b64encode(bytes(c)).decode())
        bad_n = dict(s, n=("0" if s["n"][0] != "0" else "1") + s["n"][1:])
        for bad in (bad_t, bad_c, bad_n, dict(s, t="zz"), dict(s, c="***"), dict(s, n=s["n"][:30]), {}, None,
                    {"sealed": "x"}, dict(s, c=1)):
            with self.assertRaises(seal.SealError) as cm:
                seal.open_sealed(self.SECRET, bad)
            self.assertEqual(cm.exception.args[0], "bad_seal")
        with self.assertRaises(seal.SealError):
            seal.open_sealed("another-secret", s)

    def test_nonce_must_be_16_bytes(self):
        with self.assertRaises(ValueError):
            seal.seal_bytes(self.SECRET, b"x", b"short")


if __name__ == "__main__":
    unittest.main()
