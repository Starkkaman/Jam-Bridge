# The Bridge command protocol (v1)

Shared contract between the HA integration (signs and publishes) and crew-hubd (verifies and executes). See DESIGN.md §4–§5.

## Transport
- HA publishes to `crew/<machine>/cmd`, QoS 1, **never retained**.
- crew-hubd answers on `crew/<machine>/ack`, QoS 1, not retained: `{"id", "ok": bool, "error"?: str, "result"?: {}}`.

## Envelope
```
{"v": 1, "id": "<uuid4>", "ts": <epoch ms>, "action": "<action>", "args": {...}, "sig": "<hex>"}
```
`sig` = HMAC-SHA256(machine command secret, canonical). The canonical form is
`json.dumps({"v","id","ts","action","args"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.

crew-hubd rejects (with an ack error, except when unsigned or bad-signature, which are logged and dropped without an ack):
- a retained message;
- `|now - ts| > 60 s`;
- an `id` seen in the last 10 min;
- a bad signature;
- an unknown action, invalid args, or commands disabled;
- a session that isn't live on this machine.

## Actions (args)
| action | args | rules |
|---|---|---|
| `send` | `{session, text ≤4000, urgent: bool}` | `session` = local session id. Delivered through the inbox socket, priority `now` if urgent else `next`. Text is prefixed `[From Isaac via The Bridge] `. |
| `answer` | `{session, requestId, decision: "allow"\|"deny", message? ≤500, answers?: {questionText: label}}` | Writes `~/.claude/crew-hub/answers/<requestId>.json` (0600) for the waiting hook. Error `not_waiting` / `already_answered`. |
| `interrupt` | `{session}` | Background (`kind` bg) sessions only: `claude attach <id>` in a pty, one ESC. |
| `stop` | `{session}` | Background sessions only: `claude stop <id>`. |
| `start` | `{folder (preset key), model, permissionMode, name ≤60, prompt ≤4000}` | `claude --bg --remote-control <name> -n <name> --permission-mode <m> --model <model> <prompt>` in the preset folder. The folder must be trusted. Result `{bgId, sessionId?}`. |

## Snapshot additions
- `machine.commands`: `{enabled: bool, folders: [{key, label}], models: [..], permissionModes: [..]}`. Present only when enabled.
- `session.kind`: `bg` | `interactive` | `print` (from the session registry).
- `session.controls`: the actions allowed for that session (e.g. `["send","answer"]`, plus `interrupt`, `stop` for bg).
- `session.waiting`: `{kind: "permission"|"question", requestId, tool, text, since, questions?: [{question, header?, multiSelect, options: [label…]}], hubCanAnswer: bool}`.

## `transcript` (v1, 2026-11-18): one session's recent conversation, on demand
Request args: `{session, after?: <cursor string>, limit?: 1..40 (default 20)}`. Allowed for any live session on the machine
(controls gain `"transcript"`). Never cached or stored on the machine; never part of the snapshot.

The ack's `result` is **sealed**: `{"sealed": {"n": <hex 16-byte nonce>, "c": <base64 ciphertext>, "t": <hex tag>}}`.
- Keys from the machine's command secret `S`: `ke = HMAC-SHA256(S, "bridge-transcript-enc-v1")`,
  `km = HMAC-SHA256(S, "bridge-transcript-mac-v1")`.
- Cipher (stdlib only, both sides): keystream block i = `HMAC-SHA256(ke, n || i as 8-byte big-endian)`, XOR with the
  UTF-8 JSON plaintext. Tag = `HMAC-SHA256(km, "v1" || n || c)` (encrypt-then-MAC). Random nonce per ack (`os.urandom(16)`).
  Receivers verify the tag (constant-time) before decrypting; a bad tag is an error `bad_seal`.
- Plaintext: `{"session", "cursor": <opaque>, "more": bool, "turns": [ ... ]}`, oldest first, where each turn is one of
  - `{"kind":"user", "at", "text"}`: real prompts only (not tool results, not hook/system text); a Bridge-sent message keeps its prefix.
  - `{"kind":"assistant", "at", "text", "steps": {"<verb>": count, ...}}`: assistant text, with the tool calls made since the
    previous text as counts by verb (`read`, `edit`, `write`, `search`, `run`, `web`, `agent`, `other`). No tool inputs or outputs.
  - `{"kind":"report", "at", "agent", "title", "text"}`: a subagent's final report (last assistant message).
- Limits: each text ≤ 4000 chars (clipped with "…"); whole plaintext ≤ 48 KB (drop oldest turns first, set `more`).
- Masking before sealing: anything matching secret patterns (bearer/JWT/api-key/token/password assignments, private-key
  blocks, 32+ char high-entropy runs) becomes `••••` and the turn gets `"masked": true`.
- Errors: `not_live`, `too_large`, `transcript_unavailable`.
