"""transcript (docs/COMMANDS.md § transcript): seal.py, args/controls validation, websocket crew_hub/transcript over
MQTT and webhook, refusals, bad seals, timeouts, and that the plaintext is never logged or stored."""

import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import logging
import pathlib
import sys
import time
import uuid
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.crew_hub import commands, seal
from custom_components.crew_hub.const import DATA_PENDING, DATA_STORE, DATA_WEBHOOK, DOMAIN

ROOT = pathlib.Path(__file__).resolve().parents[2]
MACHINE_SEAL = ROOT / "plugins" / "crew-hub" / "lib" / "crewhub" / "seal.py"
VECTORS = ROOT / "docs" / "evidence" / "transcript-seal-vectors.json"

SECRET = "s3cret-s3cret-s3cret"
MAC_SECRET = "mac-secret-mac-secret-mac-secret-0123456789"
MAC_HOOK = "b" * 64
NAS = {"id": "nas", "name": "NAS", "secret": SECRET}
MAC = {"id": "mac", "name": "MacBook", "transport": "webhook", "public_url": "https://hass.example.com",
       "webhook_id": MAC_HOOK, "secret": MAC_SECRET}
MANAGER = "4d2b9c71-8a0e-4f3b-b5d6-91c7e2a0f4b8"  # fixture session, interactive
MARKER = "zebra-quartz-transcript-marker"  # stands for conversation text; must never reach a log or the store
PLAIN = {
    "session": MANAGER, "cursor": "c-42", "more": False,
    "turns": [
        {"kind": "user", "at": 1763460000000, "text": "Grüße — please check " + MARKER},
        {"kind": "assistant", "at": 1763460005000, "text": "Done " + MARKER, "steps": {"read": 2, "run": 1}},
        {"kind": "report", "at": 1763460009000, "agent": "a1", "title": "Check", "text": "ok", "masked": True},
    ],
}


# ---- independent reference implementation of the contract (kept separate from seal.py on purpose) ----
def _ref_seal(secret: str, plaintext: bytes, nonce: bytes) -> dict:
    key = secret.encode("utf-8")
    ke = hmac.new(key, b"bridge-transcript-enc-v1", hashlib.sha256).digest()
    km = hmac.new(key, b"bridge-transcript-mac-v1", hashlib.sha256).digest()
    stream = b"".join(hmac.new(ke, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
                      for i in range((len(plaintext) + 31) // 32))
    c = bytes(a ^ b for a, b in zip(plaintext, stream))
    t = hmac.new(km, b"v1" + nonce + c, hashlib.sha256).hexdigest()
    return {"n": nonce.hex(), "c": base64.b64encode(c).decode(), "t": t}


# ---- seal.py ----
@pytest.mark.parametrize("size", [0, 1, 31, 32, 33, 64, 1000, 48 * 1024])
def test_seal_round_trip_and_reference(size) -> None:
    data = (("ä" + "x" * 7) * (size // 9 + 1)).encode("utf-8")[:size]
    nonce = bytes(range(16))
    sealed = seal.seal(SECRET, data, nonce=nonce)
    assert sealed == _ref_seal(SECRET, data, nonce)
    assert seal.open_sealed(SECRET, sealed) == data
    assert len(seal.seal(SECRET, data)["n"]) == 32  # random nonce
    assert seal.seal(SECRET, data)["n"] != seal.seal(SECRET, data)["n"]


def test_seal_json_and_bad_inputs() -> None:
    sealed = seal.seal(SECRET, PLAIN)
    assert seal.open_json(SECRET, sealed) == PLAIN

    def flip_hex(h: str) -> str:
        return ("1" if h[0] == "0" else "0") + h[1:]

    c = base64.b64decode(sealed["c"])
    bad = [
        None, [], {}, {"n": sealed["n"], "c": sealed["c"]},
        dict(sealed, t=flip_hex(sealed["t"])),  # bad tag
        dict(sealed, t=sealed["t"][:-2]),  # short tag
        dict(sealed, t="zz" + sealed["t"][2:]),  # not hex
        dict(sealed, n=flip_hex(sealed["n"])),  # nonce changed
        dict(sealed, c=base64.b64encode(bytes([c[0] ^ 1]) + c[1:]).decode()),  # ciphertext changed
        dict(sealed, c=sealed["c"] + "!"),  # not base64
    ]
    for item in bad:
        with pytest.raises(seal.SealError):
            seal.open_sealed(SECRET, item)
    with pytest.raises(seal.SealError):
        seal.open_sealed("another-secret-entirely", sealed)
    with pytest.raises(seal.SealError):  # authentic but not a JSON object
        seal.open_json(SECRET, seal.seal(SECRET, b"[1,2]"))
    with pytest.raises(seal.SealError):
        seal.open_json(SECRET, seal.seal(SECRET, b"\xff\xfe"))


def _vector_list(doc):
    items = doc.get("vectors", doc) if isinstance(doc, dict) else doc
    assert isinstance(items, list) and items, "unrecognised vectors file layout"
    return items


def _vector_plaintext(v: dict) -> bytes:
    for key, decode in (("plaintext_hex", bytes.fromhex), ("plaintext_b64", base64.b64decode),
                        ("plaintext", lambda p: p.encode("utf-8") if isinstance(p, str)
                         else json.dumps(p, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))):
        if key in v:
            return decode(v[key])
    raise AssertionError(f"vector without plaintext: keys {sorted(v)}")


@pytest.mark.skipif(not VECTORS.exists(), reason="machine-side vectors not written yet")
def test_seal_vectors() -> None:
    for v in _vector_list(json.loads(VECTORS.read_text())):
        sealed = v.get("sealed") or {k: v[k] for k in ("n", "c", "t")}
        secret = v["secret"]
        plaintext = _vector_plaintext(v)
        assert seal.open_sealed(secret, sealed) == plaintext
        assert seal.seal(secret, plaintext, nonce=bytes.fromhex(sealed["n"])) == sealed


def _load_machine_seal():
    name = "crewhub_machine_seal_xcheck"
    spec = importlib.util.spec_from_file_location(name, MACHINE_SEAL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


def _unwrap(sealed):
    return sealed["sealed"] if isinstance(sealed, dict) and "sealed" in sealed else sealed


@pytest.mark.skipif(not MACHINE_SEAL.exists(), reason="machine-side seal.py not written yet")
def test_cross_check_with_machine_seal() -> None:
    mod = _load_machine_seal()
    data = json.dumps(PLAIN, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # machine seals -> HA opens
    their = _unwrap(mod.seal(SECRET, data))
    assert seal.open_sealed(SECRET, their) == data
    # HA seals -> machine opens (whichever opener name the machine side uses)
    ours = seal.seal(SECRET, data)
    opener = next((getattr(mod, n) for n in ("open_sealed", "unseal", "open_seal", "open_bytes", "open")
                   if callable(getattr(mod, n, None))), None)
    assert opener is not None, "machine seal.py has no opener"
    opened = opener(SECRET, ours)
    if not isinstance(opened, (bytes, bytearray)):
        opened = json.dumps(opened, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert bytes(opened) == data


# ---- validation ----
def _snap(nas_snapshot, transcript=True):
    snap = json.loads(json.dumps(nas_snapshot))
    for s in snap["sessions"]:
        controls = [c for c in s.get("controls") or [] if c != "transcript"]
        s["controls"] = controls + (["transcript"] if transcript else [])
    return snap


def test_validate_transcript_args(nas_snapshot) -> None:
    snap = _snap(nas_snapshot)
    assert commands.validate("transcript", {"session": MANAGER}, snap) == {"session": MANAGER}
    assert commands.validate("transcript", {"session": MANAGER, "after": "c-1", "limit": 40}, snap) == \
        {"session": MANAGER, "after": "c-1", "limit": 40}
    for args in ({"session": MANAGER, "limit": 0}, {"session": MANAGER, "limit": 41},
                 {"session": MANAGER, "limit": True}, {"session": MANAGER, "limit": "5"},
                 {"session": MANAGER, "after": 3}, {"session": MANAGER, "after": "x" * 513},
                 {"session": MANAGER, "text": "hi"}, {}):
        with pytest.raises(commands.CommandError) as err:
            commands.validate("transcript", args, snap)
        assert err.value.code == "invalid_args", args
    # controls must include "transcript"
    with pytest.raises(commands.CommandError) as err:
        commands.validate("transcript", {"session": MANAGER}, _snap(nas_snapshot, transcript=False))
    assert err.value.code == "not_allowed"
    with pytest.raises(commands.CommandError) as err:
        commands.validate("transcript", {"session": "no-such-session"}, snap)
    assert err.value.code == "unknown_session"


# ---- websocket over MQTT ----
async def _setup(hass: HomeAssistant, snapshot, machines=None) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Crew Hub", data={"prefix": "crew"}, unique_id=DOMAIN,
                            options={"machines": machines if machines is not None else [NAS, MAC]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if snapshot is not None:
        async_fire_mqtt_message(hass, "crew/nas/status", "online", retain=True)
        async_fire_mqtt_message(hass, "crew/nas/snapshot", json.dumps(snapshot), retain=True)
        await hass.async_block_till_done()
    return entry


class FakeNas:
    """Stands in for mqtt.async_publish: checks the signed envelope and acks on crew/nas/ack (sealed by default)."""

    def __init__(self, ack=None, secret=SECRET, plain=None):
        self.ack, self.secret, self.plain, self.calls = ack, secret, plain or PLAIN, []

    async def __call__(self, hass, topic, payload, qos=0, retain=False, **kw):
        env = json.loads(payload)
        self.calls.append({"topic": topic, "env": env, "qos": qos, "retain": retain})
        if self.ack == "none":
            return
        ack = dict(self.ack) if self.ack else {"ok": True, "result": {"sealed": _ref_seal(
            self.secret, json.dumps(self.plain, ensure_ascii=False).encode("utf-8"), uuid.uuid4().bytes)}}
        ack["id"] = env["id"]
        hass.loop.call_soon(async_fire_mqtt_message, hass, "crew/nas/ack", json.dumps(ack))


def _cmd_sig(env: dict, secret: str) -> str:
    body = {k: env[k] for k in ("v", "id", "ts", "action", "args")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(secret.encode(), canon, hashlib.sha256).hexdigest()


async def _ws(client, i, machine="nas", session=MANAGER, **extra):
    await client.send_json({"id": i, "type": "crew_hub/transcript", "machine": machine, "session": session, **extra})
    return await client.receive_json()


def _assert_never_logged_or_stored(hass, caplog) -> None:
    # crew_hub's own records at every level, everyone's at INFO and up. (HA's websocket_api echoes every frame it
    # sends at DEBUG, which the test harness turns on; that logger is off by default and isn't ours to change.)
    for rec in caplog.records:
        if rec.name.startswith("custom_components.crew_hub") or rec.levelno >= logging.INFO:
            assert MARKER not in rec.getMessage(), rec.name
    assert MARKER not in json.dumps(hass.data[DATA_STORE].as_dict())
    assert hass.data[DATA_PENDING] == {}


async def test_mqtt_happy_path(hass, mqtt_mock, hass_ws_client, nas_snapshot, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.crew_hub")
    await _setup(hass, _snap(nas_snapshot))
    fake = FakeNas()
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", fake):
        msg = await _ws(client, 1, after="c-41", limit=10)
    assert msg["success"], msg
    assert msg["result"] == PLAIN
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["topic"] == "crew/nas/cmd" and call["qos"] == 1 and call["retain"] is False
    env = call["env"]
    assert env["action"] == "transcript" and env["args"] == {"session": MANAGER, "after": "c-41", "limit": 10}
    assert env["sig"] == _cmd_sig(env, SECRET)
    _assert_never_logged_or_stored(hass, caplog)
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("custom_components.crew_hub"))
    assert f"transcript for nas id {env['id']}: ok" in ours
    assert SECRET not in ours


@pytest.mark.parametrize(
    ("ack", "error"),
    [
        ({"ok": False, "error": "not_live"}, "not_live"),
        ({"ok": False, "error": "transcript_unavailable"}, "transcript_unavailable"),
        ({"ok": True}, "bad_seal"),  # no result
        ({"ok": True, "result": {"turns": []}}, "bad_seal"),  # unsealed plaintext is refused
        ({"ok": True, "result": {"sealed": _ref_seal("not-the-secret", b"{}", bytes(16))}}, "bad_seal"),
    ],
)
async def test_machine_errors_and_bad_seal(hass, mqtt_mock, hass_ws_client, nas_snapshot, caplog, ack, error) -> None:
    await _setup(hass, _snap(nas_snapshot))
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", FakeNas(ack=ack)):
        msg = await _ws(client, 1)
    assert not msg["success"] and msg["error"]["code"] == error
    assert hass.data[DATA_PENDING] == {}


async def test_wrong_secret_seal_refused(hass, mqtt_mock, hass_ws_client, nas_snapshot, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.crew_hub")
    await _setup(hass, _snap(nas_snapshot))
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", FakeNas(secret=MAC_SECRET)):
        msg = await _ws(client, 1)
    assert not msg["success"] and msg["error"]["code"] == "bad_seal"
    _assert_never_logged_or_stored(hass, caplog)


async def test_timeout(hass, mqtt_mock, hass_ws_client, nas_snapshot) -> None:
    await _setup(hass, _snap(nas_snapshot))
    fake = FakeNas(ack="none")
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", fake), \
            patch("custom_components.crew_hub.commands.TRANSCRIPT_ACK_TIMEOUT", 0.05):
        msg = await _ws(client, 1)
    assert not msg["success"] and msg["error"]["code"] == "no_ack"
    assert len(fake.calls) == 1
    assert hass.data[DATA_PENDING] == {}


async def test_non_admin_refused(hass, mqtt_mock, hass_ws_client, hass_read_only_access_token, nas_snapshot) -> None:
    await _setup(hass, _snap(nas_snapshot))
    fake = FakeNas()
    client = await hass_ws_client(hass, hass_read_only_access_token)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", fake):
        msg = await _ws(client, 1)
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"
    assert fake.calls == []


async def test_refusals(hass, mqtt_mock, hass_ws_client, nas_snapshot) -> None:
    await _setup(hass, _snap(nas_snapshot, transcript=False))
    fake = FakeNas()
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", fake):
        msg = await _ws(client, 1, machine="ghost")
        assert not msg["success"] and msg["error"]["code"] == "not_configured"
        msg = await _ws(client, 2)  # session controls without "transcript"
        assert not msg["success"] and msg["error"]["code"] == "not_allowed"
        msg = await _ws(client, 3, limit=41)  # schema
        assert not msg["success"]
        # once the machine offers it, the same session works
        async_fire_mqtt_message(hass, "crew/nas/snapshot", json.dumps(_snap(nas_snapshot)))
        await hass.async_block_till_done()
        msg = await _ws(client, 4)
        assert msg["success"] and msg["result"] == PLAIN, msg
    assert len(fake.calls) == 1


async def test_large_sealed_ack_over_mqtt(hass, mqtt_mock, hass_ws_client, nas_snapshot) -> None:
    """A transcript at the 48 KB plaintext cap still fits the MQTT ack size limit."""
    await _setup(hass, _snap(nas_snapshot))
    big = dict(PLAIN, turns=[{"kind": "user", "at": 1, "text": "é" * 1990} for _ in range(12)])
    assert 40_000 < len(json.dumps(big, ensure_ascii=False).encode()) <= 48 * 1024
    client = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish", FakeNas(plain=big)):
        msg = await _ws(client, 1)
    assert msg["success"] and msg["result"] == big


# ---- websocket over the webhook transport ----
def _wh_req(kind: str, data: dict | None = None) -> dict:
    req = {"v": 1, "m": "mac", "ts": int(time.time() * 1000), "nonce": str(uuid.uuid4()), "type": kind,
           "data": data if data is not None else {}}
    body = {k: req[k] for k in ("v", "m", "ts", "nonce", "type", "data")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    req["sig"] = hmac.new(MAC_SECRET.encode(), canon, hashlib.sha256).hexdigest()
    return req


async def _post(client, body):
    resp = await client.post(f"/api/webhook/{MAC_HOOK}", data=json.dumps(body),
                             headers={"Content-Type": "application/json"})
    return resp.status, await resp.read()


async def test_webhook_happy_path(hass, mqtt_mock, hass_client_no_auth, hass_ws_client, nas_snapshot, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.crew_hub")
    await _setup(hass, None)
    http = await hass_client_no_auth()
    assert (await _post(http, _wh_req("snapshot", _snap(nas_snapshot))))[0] == 200
    ws = await hass_ws_client(hass)
    with patch("custom_components.crew_hub.commands.mqtt.async_publish") as publish:
        await ws.send_json({"id": 1, "type": "crew_hub/transcript", "machine": "mac", "session": MANAGER})
        hub = hass.data[DATA_WEBHOOK]
        for _ in range(50):
            if hub.queued("mac"):
                break
            await asyncio.sleep(0.01)
        code, body = await _post(http, _wh_req("poll"))
        assert code == 200
        (env,) = json.loads(body)["commands"]
        assert env["action"] == "transcript" and env["args"] == {"session": MANAGER}
        assert env["sig"] == _cmd_sig(env, MAC_SECRET)
        sealed = _ref_seal(MAC_SECRET, json.dumps(PLAIN).encode("utf-8"), uuid.uuid4().bytes)
        code, body = await _post(http, _wh_req("ack", {"id": env["id"], "ok": True, "result": {"sealed": sealed}}))
        assert code == 200 and json.loads(body) == {"ok": True}
        msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"] == PLAIN
    publish.assert_not_called()
    _assert_never_logged_or_stored(hass, caplog)
