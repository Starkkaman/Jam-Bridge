# The Bridge: architecture spec (v1, 2026-11-18)

Status: **spec only, nothing built.** One Home Assistant app (add-on) that merges Crew Hub (Claude sessions) with Isaac's n8n agent
crew behind ingress, in the Jam look. Isaac: "make an add-on … that way my previous chats don't vanish when I close it" and "merge
with Crew Hub, rename it the bridge". Views: Overview, Crew, Chat, Tickets, Tasks, Library, Inbox. The NAS agent owns the n8n side.

## 1. Verified facts
Sources: HA Core 2026.10.4 (`~/.local/opt/ha-test-venv`; "Core" paths are under `homeassistant/`), Supervisor tag **2026.11.0**
(github.com/home-assistant/supervisor; other paths are under `supervisor/`), the docs at contributors.home-assistant.io/apps/, spikes
in the session scratchpad (`bridge-spike/`). The n8n URLs were not called from here (NAS agent's rule); the fixtures are real responses.

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Can `SUPERVISOR_TOKEN` on `ws://supervisor/core/websocket` pass crew_hub's `require_admin`? | **Yes, as HA's "Supervisor" system user, which is an admin.** The Supervisor checks that the token belongs to an app with `homeassistant_api`, sends `auth_ok` itself, then opens its own Core connection as the Supervisor user: pre-authenticated over the Unix socket, or with that user's refresh token over TCP. Core creates that user in the admin group and moves it back to admin on every start. The proxy blocks only `supervisor/*` and `hassio/*` frames. **No crew_hub change is needed.** | `api/proxy.py:48,324-371,391-402`, `homeassistant/api.py:213-260,273-294` (Supervisor's); Core `components/http/auth.py:189-211`, `components/websocket_api/auth.py:71-83`, `components/hassio/__init__.py:175-221`, `auth/models.py:91-96`, `components/websocket_api/decorators.py:54-67`. The docs' communication guide says "your app acts as an administrator". Spike (pytest-HA, 2 tests passed). A system admin user made like hassio's got: `crew_hub/subscribe` ok, `crew_hub/command` → `{ok:false, invalid_args}` (past the admin check), `crew_hub/dismiss` → `{ok:true}`, and `call_service crew_hub.send_message` past services.py's admin check. A system **non**-admin user got unauthorized on all four. |
| 2 | Which ingress headers name the HA user, can we trust them, and can the panel be admin-only? | The Supervisor drops any `X-Remote-User-Id/-Name/-Display-Name/-Groups` the client sends. It then sets them from the session's user (Groups = effective group ids; owners get `system-admin` folded in). `panel_admin` (default true) is enforced **on the server**: a non-admin gets 403 before the request reaches the app. The app only ever sees 172.30.32.2. The headers are "not guaranteed" (no user found), so a request without them is anonymous and read-only. | `api/ingress.py:47-63,191,400-419`; `const.py:112-115,627-657`; `docker/network.py:188-190` (Supervisor = 172.30.32.2); docs /apps/guides/ingress/; Groups header from PR #7398 (2026-10-21). |
| 3 | Does /data persist, should the app be in backups, and does SQLite WAL work on HA OS? | /data is the app's private read-write volume. It survives restarts and updates and is removed on uninstall. Backups are hot by default. `backup_pre` runs inside the container, and a non-zero exit fails this app's backup. A restore wipes /data and then copies the backup back. Callsheet already runs a WAL SQLite in /data on this host. **Decision: include the app in backups** (it holds the only long-term copy of the chats), as a hot backup with a `backup_pre` snapshot (§3). | docs /apps/reference/configuration/ (`map` data, backup keys); `apps/app.py:1443-1480,1728-1740`; `apps/validate.py:229-231,531-534`; Callsheet `app/store.py:238` (public-main). |
| 4 | Does outbound traffic from the container reach `<nas-ip>` with the HA host's IP? | **Yes by construction, not yet seen live.** Apps sit on the Docker bridge `hassio` (172.30.32.0/23). It is created with only a bridge-name option, so Docker's default IP masquerade applies. Packets leave with the address of the host interface that routes to the NAS subnet: that is `<ha-host-ip>` if it is the host's only interface. Not testable from here. On first run, check that n8n's allow-list accepts the calls (§7, `n8n.firstContact`). | `docker/network.py:32-50`, `const.py:69` (`DOCKER_IPV4_NETWORK_MASK` 172.30.32.0/23); Docker bridge driver docs (`enable_ip_masquerade` defaults to true). |
| 5 | Does SSE work through ingress? | Yes. Both hops stream a response without Content-Length chunk by chunk, with no total timeout. Core never compresses `text/event-stream`, and the Supervisor adds `X-Accel-Buffering: no`. Spike: events sent 0.3 s apart arrived 0.3 s apart through a streaming proxy that strips the prefix. | Core `components/hassio/ingress.py:49,184,212-245`, `components/hassio/http.py:232-237`; Supervisor `api/ingress.py:277-350`. |
| 6 | Can one repo hold both the HACS integration and the app? | Yes. The Supervisor finds apps by globbing `**/config.{json,yaml,yml}` (dot folders and `rootfs` skipped). The repo has no such file today and is public. | `store/data.py:151-165`; `git ls-files`; GitHub API 200 without auth. |

## 2. Components
```
 Browser (HA frontend iframe, companion app, Isaac's reverse proxy; the HA login is the authentication)
   │ /api/hassio_ingress/<token>/…  (relative URLs; SSE on api/events)
   ▼
 HA Core ingress ──► Supervisor ingress 172.30.32.2 (session check, panel_admin, sets X-Remote-User-*) ──► app :8099
 ┌─ The Bridge app container (slug bridge) ───────────────────────────────────────────────────────────────────────┐
 │ server.py  aiohttp, one asyncio loop: static UI · api/* JSON · api/events SSE · ingress-only middleware         │
 │ crew.py ── ws://supervisor/core/websocket (SUPERVISOR_TOKEN) ──► Supervisor proxy ──► Core: crew_hub/* (admin)  │
 │ n8n.py  ── HTTP, NAT'd to the HA host IP ──► n8n <nas-ip>:<n8n-port> /webhook/agent-hub/* and /webhook/bridge/*  │
 │ jobs.py    chat sender, library apply, pollers          db.py ── SQLite /data/bridge.db (WAL)                     │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
crew_hub (the HACS integration) keeps MQTT/webhook transport, HMAC signing and the Lovelace card; the app only uses it.

## 3. Repo layout and packaging
```
repository.yaml   name "The Bridge", url https://github.com/Starkkaman/Jam-Bridge, maintainer "Isaac" (HACS ignores it)
bridge/           config.yaml Dockerfile run.sh (LF) CHANGELOG.md DOCS.md icon.png logo.png translations/en.yaml
  app/            server.py api.py crew.py n8n.py jobs.py db.py backup.py  static/ (index.html app.css app.js favicon.svg fonts/)
tests/bridge/     test_*.py fake_n8n.py fake_core_ws.py ingress_proxy.py  fixtures/n8n/ (moved here from bridge/fixtures/)
```
- `config.yaml`: `name: "The Bridge"`, `version: "0.1.0"`, `slug: bridge` (permanent: it names the data folder), `arch: [amd64,
  aarch64]`, `init: false`, `startup: application`, `ingress: true`, `ingress_port: 8099`, `panel_title: "The Bridge"`,
  `panel_icon: "bridge:captain-chair (custom iconset from crew_hub; was mdi:bridge)"`, `panel_admin: true` (written out), `homeassistant_api: true`, `backup: hot`, `backup_pre: "python3
  /app/backup.py"`, `backup_exclude: [bridge.db, bridge.db-wal, bridge.db-shm]`, `options/schema: n8n_url (url, default
  http://<nas-ip>:<n8n-port>; the real address is set in the add-on options, never in the repo)`. No `ports`, `hassio_api`, `map` or host network.
- `Dockerfile`: `ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24` (as in Callsheet; no build.yaml), `apk add
  tzdata`, `pip install aiohttp==3.14.5` (PyPI has cp314 musllinux wheels for amd64 and aarch64). The Supervisor builds it on install.
- Backup: `backup.py` checkpoints the WAL and copies the DB to `/data/snapshot/bridge.db` with sqlite3's backup API (exit 0 if
  there is no DB yet). The server writes the same snapshot on a clean stop. When `bridge.db` is missing (after a restore), `run.sh` copies the snapshot back.
- **No other `config.json/yaml/yml` anywhere in the repo** (the Supervisor would see a broken app). Versions work as in Callsheet:
  `config.yaml` `version` = `APP_VERSION` in `api.py` (tested; it drives the `?v=` asset URLs), plus a CHANGELOG.md entry.

## 4. Server choice: aiohttp, one pinned dependency
- crew_hub needs a **long-lived WebSocket client** (subscribe, pushes, the Supervisor's 30 s heartbeat pings at `proxy.py:336`,
  fragmented frames, big snapshots). The stdlib has none, and a hand-written RFC 6455 client is security-sensitive code.
- Many waits run at once: N SSE streams, chats up to 120 s, applies up to 900 s, five pollers. One asyncio loop holds them all,
  where `http.server` would pin a thread for each. aiohttp is what HA runs (3.14.5 in the venv): server, SSE (`StreamResponse`),
  an HTTP client with per-call timeouts and a WS client with autoping. The test fakes use it too.
- Callsheet's rules still apply: the stdlib for the rest (sqlite3 on one writer thread via `asyncio.to_thread`), no new dependency
  without Isaac's OK, no runtime downloads or CDNs, fonts bundled (Jost and IBM Plex Mono from `crew_hub/www/fonts`).
- **Long calls never hold a browser request.** A write stores its row, starts a job and returns `202 {row}`. The result comes over
  SSE, and it is in the DB even if the browser was closed: that is the "chats don't vanish" promise.

## 5. Data model (SQLite `/data/bridge.db`, mode 0600)
PRAGMAs `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`; migrations by `user_version`; epoch ms
UTC. No tokens or secrets in rows. Messages and inbox rows are never pruned (that is the point of the app).
```
threads   (id TEXT PK "th_<uuid>", kind 'n8n'|'claude', target,   -- n8n: agent id or 'crew'; claude: '<machine>/<session id>'
           title, target_name (last display name seen), created_by_id, created_by_name, created_at, updated_at,
           last_message_at, archived INT DEFAULT 0, n8n_conversation_id)  -- = id; kept apart so a reset can rotate it
messages  (id INTEGER PK, thread_id FK, role 'user'|'agent'|'status', source 'bridge'|'n8n'|'crew_hub'|'snapshot',
           author_id, author_name, text (user ≤4000, agent ≤64 KB), state 'queued'|'sending'|'replied'|'delivered'|'failed'|
           'timeout'|'unknown'|'dropped', error, client_id UNIQUE, created_at, finished_at, duration_ms)
read_state(user_id, scope 'thread:<id>'|'inbox'|'tickets'|'library', last_read_at, PRIMARY KEY (user_id, scope))
tickets   (id INTEGER PK (n8n id), type, agent, agent_match (agent id or NULL), title, details, status, source, resolution,
           comments_json (parsed; raw text kept when invalid), created_at, updated_at, fetched_at)
inbox     (id INTEGER PK (n8n id), source, title, message, created_at, first_seen_at)   -- n8n keeps only the last 50
library   (date TEXT PK, finished, notes_json, errors_json, fetched_at)
findings  (id TEXT PK, date FK, kind, severity, title, detail, fix, applied INT, applied_seen_at)
actions   (id INTEGER PK, at, user_id, user_name, kind, target, summary (clipped, never message text), client_id UNIQUE,
           outcome 'ok'|'refused'|'error'|'timeout', error)   -- audit log of every write, kept 1 year
cache     (key PK 'n8n.status'|'crew.machines'|'runs:<workflowId>', json, fetched_at, ok INT, error)   -- last good copy
settings  (key PK, value_json)   -- replay_after_hours, per-user mode light|dark|auto
```
`actions.kind`: chat.send, ticket.create, ticket.update, library.apply, claude.send/answer/interrupt/stop/dismiss.

## 6. API between the page and the server (relative URLs under `api/`)
Every mutating request: `X-Bridge: 1` (a CSRF guard like `X-Callsheet`), JSON ≤64 KB, a `clientId` (uuid, deduplicated in
`actions`/`messages`), and a user (§9). Errors are `{error, code}`.

| Endpoint | Purpose |
|---|---|
| `GET api/state` | version, user {id, name, display, canWrite}, health {n8n {ok, at, error}, crew {connected, at}}, unread counts |
| `GET api/overview` | health; **needs you** (Claude `waiting`, n8n `problems[]`, agents `error`/`down`, tickets with a new "NAS agent" comment, unapplied findings); **running now** (n8n `running[]`, Claude sessions working) |
| `GET api/crew` | Claude machines, sessions and plans (crew_hub view); n8n agents grouped `agents` / `services` / `system` |
| `GET/POST api/threads`, `PATCH api/threads/{id}` | list with unread counts per user / open {kind, target} (reuses the open thread of that target) / rename, archive |
| `GET api/threads/{id}/messages?before=&limit=50` | history, newest last |
| `POST api/threads/{id}/messages`, `…/{mid}/retry` | {text, urgent?} → 202 {message, state queued} / resend a failed or unknown one |
| `POST api/read` | {scope, at} |
| `GET api/tickets?status=`, `POST api/tickets`, `POST api/tickets/{id}` | cached list / create / {status?, resolution?, comment?} |
| `GET api/tasks`, `GET api/tasks/runs?workflowId=` | n8n runs (cached), the library agent's task, Claude plans / last 20 runs of one workflow |
| `GET api/library`, `POST api/library/apply` | last report / {ids or "all", confirm: true, force?, forceConfirm?} → 202 {job} |
| `GET api/inbox?before=` | history, including rows older than n8n's last 50 |
| `POST api/claude/command`, `POST api/claude/dismiss` | {machine, action send/answer/interrupt/stop, args, confirm?} / crew_hub dismiss |

SSE `GET api/events`: `hello {version, user, serverTime}`, `crew {machines}` / `crew.machine {id, data}` (crew_hub's events),
`n8n {status, at, error?}`, `thread`, `message` (a new row or a state change), `tickets {ids}`, `inbox {count}`, `library {date,
applied[]}`, `job {id, kind, state, detail}`, and a `: ping` comment every 20 s for reverse proxies. No replay: after a reconnect,
`hello` makes the page refetch its view. "Visible" (§7) means at least one SSE stream is open.

## 7. n8n adapter (`n8n.py`)
- **Allow-list by construction** (like Callsheet's `ha.py`): only the 18 (method, path) pairs of the NAS contract (§14 adds the four OpenCode ones) under `n8n_url`.
  No redirects, no secrets, bodies ≤1 MB (status 2 MB). The first response after a start is logged once (HTTP status only) as
  `n8n.firstContact`, which settles verification 4. Timeouts: GETs 15 s, chat **120 s**, ticket POSTs 20 s, apply **900 s**.
  One request in flight per endpoint (shared; a tick is skipped while the previous call is open). Errors: agent-hub/status retries
  every 60 s (NAS agent's request), the others back off ×2 up to 5 min; "n8n unreachable since …" shows over the `cache` copy.

| GET | visible | nobody watching | also |
|---|---|---|---|
| agent-hub/status | 30 s | 5 min | right after a chat reply, ticket write or apply |
| bridge/tickets | 60 s | 15 min | right after our ticket writes |
| bridge/inbox | 2 min | 10 min (nothing falls past the last 50) | — |
| bridge/library | on open, then 10 min | 60 min | every 15 s while an apply job runs |
| bridge/runs?workflowId= | on demand (Tasks, agent detail), cached 60 s | never | n8n workflows only; `svc:*` have no runs |

- **Agents:** `id` is the key; `name` is only a label and can change. `n8n:<workflowId>` agents carry `workflowId`, `svc:*` are
  services, and "Agent Hub: *", "The Bridge: API" and "Phone Reply Agent" are grouped as **system** (one name list). Unknown
  `state`s and ticket `type`s (the fixtures have `test`) show as plain labels. `tickets.recent[]` has `created`, `bridge/tickets` has `createdAt`.
- **Tickets:** `agent` is free text, matched loosely: casefold, alphanumeric words minus {agent, the, a}, then an equal word set
  or containment ("Jellyfin library agent" ↔ "Library agent (Jellyfin)"); otherwise "Other". Create sends `agent` = the display
  name. Limits are checked first (title 120, details 4000, comment 4000, resolution 2000). `comments` (a JSON string or null) is
  parsed defensively. Updates append, with `author` = the HA user's display name; "NAS agent" marks agent replies.
- **Chat:** `agent-hub/chat {query, conversation_id: thread.n8n_conversation_id, user_name, agent?: display name}`. One message
  in flight per thread; later ones are `queued`, and `dropped` after 10 min. A `sending` row found at start becomes `unknown`
  ("The Bridge restarted while waiting"); a timeout becomes `timeout`. Neither is resent automatically (agent actions may have
  run); only **Retry** resends. The Bridge never sends "yes" for the user and has no quick-reply chips: destructive agent actions
  need a typed "yes". n8n keeps 10 turns; we keep everything. With `replay_after_hours` set (off by default), the first message
  after a gap carries a clipped summary of the last turns.
- **Library apply:** a confirm, then a job: `bridge/library/apply {ids|"all", by: display name}`. The page shows "Applying…" and
  re-reads the `applied` flags every 15 s. The 17:00–01:00 quiet hours are shown up front. `{ok:false, error:"evening quiet
  hours…"}` is shown as is, and only then does "Apply anyway" (`force: true`) appear, behind a **second** confirm.
- **Inbox:** agent digests and alerts only. Show the title and a two-line clip; tap for the full text (it can name hosts: admin-only panel).
- **Try again and library scan (added 2026-11-18, from the NAS agent):** `agent-hub/status` agents carry `rerun` (bool) and
  optionally `rerun_via` (`svc:media-agent` → `bridge/library/scan`). Two POSTs join the allow-list:
  `bridge/run-now {workflowId, by}` → `{ok, workflowId, name, startedAt, note}` (runs in the background; HTTP 500 = not
  re-runnable → job error `not_rerunnable`) and `bridge/library/scan {hours: 24, by}` → `{started, state, task}` (read-only, any
  time; 409 → job `refused`, code `scan_running`). The page calls `POST api/agents/rerun {agentId, confirm: true}` (202 + job;
  only agents with `rerun: true` in the cached status, else 409 `not_rerunnable`; without `confirm` 400 `confirm_required` with
  the hint as `error`) or `POST api/library/scan {hours?}` (202 + job, no confirm). Agents in `api/crew`/`api/tasks` carry
  `canRerun` and `rerunHint` (the confirm copy: both re-runnable workflows notify the owner's phone again). After run-now the job
  polls `bridge/runs?workflowId=` every 10 s (up to 15 min) for the new `integrated` execution and pushes `job` events with
  `detail.step` (starting → started → running → finished) and `detail.run {id, status, startedAt, stoppedAt}`. Both actions are
  audited with the HA user (`agent.rerun`, `library.scan`). A real re-run is not proven yet (it notifies the phone): verify the
  first one together with the owner after install.

## 8. crew_hub adapter (`crew.py`)
- One websocket to `ws://supervisor/core/websocket`, auth with `SUPERVISOR_TOKEN` (read from the environment on each connect;
  never logged, stored or returned). Reconnect after 1, 2, 5, 10, 30 s; resubscribe on reconnect.
- **Frame allow-list** (raises before sending): `auth`, `crew_hub/subscribe`, `crew_hub/get`, `crew_hub/command` with `send`,
  `answer`, `interrupt` or `stop`, `crew_hub/dismiss`, `unsubscribe_events`. Nothing else, and no REST. The token is an HA admin
  credential (docs warning), so this list is what keeps the app narrow.
- Claude chat is **one-way**: `send {session, text ≤4000, urgent}` shows as delivered or with crew_hub's error code. When that
  session goes idle or waiting, a `status` row shows its snapshot `summary` (≤200 chars, the last assistant line): a hint, not a
  reply. `interrupt` and `stop` (background sessions, per `session.controls`) need `confirm: true` from a dialog. `answer` has no
  confirm (as in the card). `start` stays in the Lovelace card for v1.
- **Attribution:** crew_hub sees every call as the "Supervisor" user, so `actions` is the record; the daemon's prefix is fixed (§12).

## 9. Security model
- **Ingress only:** the server listens on 0.0.0.0:8099 and accepts only 172.30.32.2, 127.0.0.1 and ::1 (aiohttp's `request.remote`
  is the peer; X-Forwarded-For is never trusted). No `ports`, no login page, no absolute URLs or redirects to `/…`. `panel_admin`
  is enforced by the Supervisor (§1, row 2).
- **Every write names an HA user:** it needs `X-Remote-User-Id` and `X-Remote-User-Groups` ∋ `system-admin` (Supervisor ≥
  2026.11.0), or it gets 403 "open The Bridge from Home Assistant" (loopback and dev too; tests set the headers). Reads need only
  an allowed peer. The display name goes to n8n (`user_name`, `author`, `by`, ticket `source` "The Bridge · <name>"), and every
  write lands in `actions`.
- **No secrets:** n8n allow-lists by source IP. The only credential is `SUPERVISOR_TOKEN` (environment only); options hold the n8n
  URL only. Backups contain chat text, so HA's backup encryption covers it.
- **Confirms:** library Apply (force needs a second one), Claude Stop and Interrupt. **Data is text:** agent output, tickets,
  inbox items and summaries use `textContent` with `pre-wrap`; no markdown, HTML or auto-links in v1; the app never acts on AI output.
- Headers: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; frame-ancestors 'self'`, `nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-cache` (a week for versioned assets). The page calls nothing outside.

## 10. Rename plan (display names only)
These stay (ids): domain `crew_hub`, `crew_hub/*` websocket types, entity ids (`sensor.crew_hub_working`, `binary_sensor.crew_hub_needs_me`,
`binary_sensor.crew_hub_<m>_online`; the registry keeps them), Store keys `crew_hub.*`, MQTT `crew/`, `~/.claude/crew-hub/`, plugin ids
`crew-hub@jamboard` and `crew-flow`, `custom:crew-hub-card`, `/crew_hub/…` URLs, `<!-- crew-hub:plan-file -->` markers, the repo name.
"Crew Hub" becomes "The Bridge" in:
1. Integration: `manifest.json` `name`, `const.py` `NAME` (the device name, so entity friendly names change), `hacs.json` `name`,
   `strings.json` and `translations/en.json` (titles, "already set up" aborts, field texts), `services.yaml` (6 descriptions),
   `__init__.py:144` webhook name `f"Crew Hub {machine}"`, and log texts in `__init__.py`/`store.py` (optional).
2. Card (`card/crew-hub-card.js` and its `www/` copy): the brand `h2`, "Crew Hub integration not available", "isn't set up in Crew
   Hub"; texts in `card/verify-controls.js` and `card/preview-hass.html`.
3. Plugin (every machine needs the update): `lib/crewhub/commands.py:36` PREFIX `[From Isaac via Crew Hub] ` (sessions read
   it; §12), `DENY_PREFIX` (`commands.py:37`, `hook.py:26`), `pair.py` prompts, `skills/crew-hub-setup/SKILL.md`, `plugin.json` and
   `hooks/hooks.json` descriptions, the `templates/plan-file-rule.md` body (rewritten in CLAUDE.md on the next pairing), and
   "Crew Hub dashboard" in crew-flow's `plugin.json` and `SKILL.md`.
4. Docs (README, CLAUDE.md, filemap.md, DESIGN, COMMANDS, WEBHOOK, WINDOWS) and the tests that assert these strings.
5. In HA (Mr Manager): the "Crew" dashboard at `/crew-hub` (keep or retire, §12) and the integration entry title.

## 11. Test plan
- **Unit** (`python3 -m unittest discover -s tests`, with aiohttp's test utilities):
  - db: migrations, WAL, `clientId` dedupe, `sending` → `unknown` at start, queued → dropped.
  - n8n: allow-list, timeouts, cadences, ticket ↔ agent matching on the fixtures, null or invalid `comments`, limits, the quiet-hours text, `force` only with `forceConfirm`.
  - crew: frame allow-list, auth failure, reconnect and resubscribe, passthrough, Stop/Interrupt confirms.
  - server: 403 for other peers and for writes without the user headers or `system-admin`, `X-Bridge`, the 64 KB limit, headers,
    no absolute URLs in static files, `config.yaml` version = `APP_VERSION`, no stray `config.*`.
  - backup.py: a snapshot taken during writes is consistent, and the `run.sh` restore works.
- **Fakes:** `fake_n8n.py` (aiohttp) serves `tests/bridge/fixtures/n8n/*.json` and records POSTs; it can delay (a 130 s chat
  times out, apply flips `applied` over time), answer quiet hours, or fail. `fake_core_ws.py`: the Supervisor auth dance, crew_hub
  replies from `card/fixtures/snapshot-sample.json`, a 30 s heartbeat, drops on command.
- **HA harness** (`tests/ha`): keep the §1 spike as `test_supervisor_user.py`. A future crew_hub check that refuses system users then fails here first.
- **Playwright** (`pw-node`, outside the repo) through `ingress_proxy.py`, which strips `/api/hassio_ingress/<token>/`, injects
  `X-Remote-User-*` and streams SSE. Every view at 1280 and 400 px in day, night and auto: fonts, overflow, contrast, no foreign
  requests, and a chat reply that arrived while the page was closed.
- **Once on the real HA** (Mr Manager): install from the repository, check that a non-admin gets neither the sidebar entry nor
  access (403), send one chat and one ticket comment, read `n8n.firstContact`, then take a backup and restore it.

## 12. Open questions
**Isaac:** 1. Keep the Lovelace card and the "Crew" dashboard next to The Bridge, or retire them? (The app needs crew_hub either
way.) 2. Should any HA admin be able to write, or only you? (Default: any admin, and every write is logged with the name.)
3. `[From <name> via The Bridge]` needs a `from` argument in `send` (protocol v1's `_only` refuses unknown args), with crew_hub,
crew-hubd and every machine's plugin updated together. Do that, or keep "Isaac"? 4. For v2: start sessions here, markdown in
replies, a replayed summary after long gaps? Each view's layout is a separate Jam design task (`design/callsheet-harmony/`).

**NAS agent:** 1. Answered 2026-11-18: the cadences are fine; successful executions of "Agent Hub: Status" and "The Bridge: API"
are no longer saved; no push (the add-on stays inbound-free). 2. Is there a maximum chat `query` length? Can `agent` take the id (a rename
would orphan threads)? 3. Can `bridge/inbox` take `?after=<id>`? 4. Source IP: verified 2026-11-18 (Mr Manager curl from the Terminal & SSH add-on, 172.30.33.1 → 200): add-on traffic
NATs out as the HA host's LAN address, so no allow-list change and no secrets are needed.

**Mr Manager:** is the installed Supervisor ≥ 2026.11.0 (server-side `panel_admin`, Groups header)? Then add the repository
https://github.com/Starkkaman/Jam-Bridge to the app store.

**NAS agent answers (2026-11-18, round 2):**
- Tickets carry `agent_id` (POST /agent-hub/ticket accepts it; must match `^(n8n|svc|claude):[\w:.-]{1,80}$`, else stored
  empty; `claude:<session id>` reserved for Claude sessions). /bridge/tickets rows include it; older tickets have null → fall
  back to loose name matching.
- Ticket writes (corrected 2026-11-19): `POST /agent-hub/ticket` only **creates** (title or details required).
  Updates go to `POST /webhook/bridge/ticket-update` with `{id, status?, resolution?, comment?, author?}`, no `action`
  field. Status: open | in_progress | needs_isaac | awaiting_approval | done | closed. Until 0.2.1 the add-on sent
  updates to the create endpoint, which refused them as empty tickets.
  Since 2026-11-19 both endpoints answer validation errors with 400 `{"ok": false, "error": "<reason>"}` (e.g. "ticket <id>
  not found", "nothing to update"); the Bridge shows it as "n8n: <reason>".
- `GET /webhook/bridge/library/precheck` → `{playing, sessions, titles[≤5], evening, busy, undo: true, undo_note}`;
  playing/sessions null + `error` if Jellyfin is unreachable. The quiet-hours go/no-go poll reads it (Apply already skips
  anything playing, so it's a hint, not a lock).
- /bridge/library returns the full report (e.g. 128 findings, ~40 KB), no paging: page client-side. The fixture is cut to 25.

**Isaac's decisions (2026-11-18):**
1. "Apply all safe" = low-severity `replace_trailer` findings (undoable).
2. An offline machine is quiet (dashed "Offline") UNLESS it misses a scheduled task: then it's an alert. Source: Claude Code
   puts `session_crons` in the Stop/SubagentStop hook payload (seen in docs/evidence/hooks-2.1.295-sample.jsonl, empty there);
   crew-hook keeps its schedule fields (no prompt text), crew-hubd publishes `session.crons[{id, schedule, nextRunAt}]`, and the
   Bridge alerts when a machine is offline past a cron's nextRunAt. Field shape to verify on the current CLI before building.
3. Theme setting: Follow HA (default) / Night / Day, stored per HA user in the add-on's settings table.
   How Follow HA works (frontend, 2026-11-18): ingress serves the page in an iframe on HA's own origin, so the page reads
   `parent.document.querySelector('home-assistant').hass.themes.darkMode` (in a try/catch, re-read every 2 s) and uses
   `prefers-color-scheme` only when that throws or is missing. Verified against a same-origin fake HA parent (HA light → Day,
   HA dark → Night, while the device preferred dark); to confirm once on the real HA. Settings says which source is in use.
4. Chat history kept 90 days (nightly prune of messages older than 90 days; threads with no messages left are removed).

**Agent output + direct chat (n8n side live 2026-11-18; Isaac: "see the output of each agent when selected, and interact
directly with them if they're set up for that"):**
- `GET /webhook/bridge/run?id=<executionId>` → `{id, workflowId, status, mode, startedAt, stoppedAt, lastNode, summary (≤4 KB
  plain text or null), tools[{tool, calls, error}], output (last node JSON as a string, ≤20 KB), error}`; secrets masked by n8n.
  "Agent Hub: Status" and "The Bridge: API" keep no successful runs → system, Runs hidden.
- `/status` agents now carry `chat` (bool) and `role` (downloads, library, security, general, system, service). Direct chat =
  `POST /agent-hub/chat {query, conversation_id, user_name, agent_id}` (or `role`) → `{output}`, 120 s; one conversation_id per
  thread (memory is per id). chat:true today: Download Daily Summary, Download Watcher, Security Weekly Review, svc:netaudit,
  svc:media-agent, Phone Reply Agent, Agent Hub Chat.
- `GET /webhook/bridge/agent-output?id=svc:…` → `{id, updated, text (markdown, ≤20 KB)}` for services; unknown id → 500.
- Claude side (to design): on-demand session conversation view + two-way thread, encrypted in transit with the machine's
  command secret, secrets masked, no raw tool outputs, never in snapshots; subagents are reached through their session.

**Second factor for n8n (agreed 2026-11-18, NAS agent's proposal):** every /agent-hub/* and /bridge/* webhook will require
`X-Bridge-Key` in addition to the HA-host allow-list (/agent-hub/chat also accepts Basic auth with the same secret, for HA's
webhook_conversation). The add-on gets it as an add-on option `n8n_key` (schema `password`), stored in HA's add-on config,
never in the repo, never logged, never sent to the browser; the server adds the header to every n8n call and reports
"n8n key missing or wrong" (401/403) as a setup banner. Isaac pastes the key himself. The NAS agent switches the endpoints over
only when the add-on is ready to test.
- Paths at switch time: the add-on chats via **POST /webhook/bridge/chat** (X-Bridge-Key; same body/response as
  /agent-hub/chat, live now without the check). /agent-hub/chat moves to Basic auth for webhook_conversation only.
  /agent-hub/status, /agent-hub/ticket and all /bridge/* take X-Bridge-Key. Switch order: Isaac pastes the key (add-on
  options + Mr Manager) → the add-on confirms 200s with the key → the NAS agent flips all 4 workflows in one redeploy.


**The Engineer in Tickets (0.1.3, from the n8n owner, 2026-11-18):** the Engineer is an OpenCode agent working the ticket
inbox. Ticket statuses are `open | in_progress | needs_isaac | awaiting_approval | done | closed` (the server accepts only
these). `needs_isaac` = the Engineer wants direction ("Needs you", caution ▲, comment box ready with "The Engineer is
waiting for your direction"); `awaiting_approval` = a sandboxed fix is ready ("Ready to review": Approve, lit, and Request
changes). Approve is `POST api/tickets/{id} {approve: true}` → a ticket update with the comment "Approved" (author = the
HA user, audited as `ticket.update` / `approve`); promotion to live still needs a click in OpenCode, which the page says.
Request changes is a plain comment; a human comment wakes the Engineer within 15 min. Comment authors "Engineer" and
"NAS agent" are agents, anyone else is the human. Both statuses feed Now's needs-you count and panels
(`needsYou.ticketsNeedYou`, `unread.ticketsNeedYou`), after the Claude prompts; the status control sets only
Open / In progress / Done. "Engineer: notify" and "Engineer: dispatch" are system workflows.

## 13. Agent output, Talk and Claude conversations (0.2.0)

Design: design/bridge/output-talk (mockup, notes, screenshots). Isaac's decisions (2026-11-18): (1) "If I can't chat
with it, it shouldn't be in Chat"; (2) Claude steps are counts per reply, never tool names or inputs; (3) agents that
are off but have `chat: true` still get Talk.

**Endpoints** (all audited with the HA user in `actions`):

| Endpoint | Source | Cache | Notes |
|---|---|---|---|
| `GET api/agents/{id}/runs/{runId}` | n8n `/bridge/run?id=` | 60 s (15 s while running) | 404 `no_runs` for system, untracked and workflow-less agents; 404 `no_run` when n8n has no such run or it belongs to another workflow. Audit `agent.run`. |
| `GET api/agents/{id}/output[?fresh=1]` | n8n `/bridge/agent-output?id=svc:…` | 30 s; `fresh` reuses only a copy < 5 s old | Workflows: 404 `no_output`. Unknown service: 404 `not_found`. A known service n8n has no report for: 200 `{output: null, reason: "no_output"}`. Audit `agent.output` (open / refresh). |
| `GET api/sessions/{machine}/{session}/conversation[?after=<cursor>][&fresh=1]` | crew_hub websocket `crew_hub/transcript {machine, session, after?, limit: 20}` | memory only, ≤ 60 s | Admins only. 503 `crew_disconnected`, 404 `unknown_session`, 409 `offline`, 409 `no_transcript`, 504 `crew_timeout`, 502 `<machine error code>`. `not_live` / `transcript_unavailable` answer 200 `{gone: true, reason}`. Audit `claude.transcript` (open / poll / refresh, "· cached"). |
| `POST api/sessions/{machine}/{session}/conversation/close` | n/a | n/a | The page left the conversation: the server drops it now. |

**Conversations are never stored.** `conversation.py` keeps the shaped turns in process memory, keyed by
machine/session, for at most 60 s. They are dropped earlier on close, when the last page disconnects (SSE unsubscribe)
and on restart. Nothing from a transcript goes into SQLite, the snapshot or the SSE stream; a test greps the data
folder for a sentinel string from a fake transcript. Turns reach the page as
`{id, k: user|assistant|report, at, text, via?, steps?, agentId?, title?, masked?}`. A user turn that starts with the
Bridge prefix (`[From … via The Bridge]`) has the prefix removed and `via: "bridge"`. `steps` keeps only the contract's
counts (read, search, edit, write, run, web, agent, other). The machine's mask `••••` becomes one token that the page
draws as •••• plus a MASKED tag.

**Polling.** While a conversation is on screen (the session page `#session/<machine>/<id>`, or a Claude thread in
Chat), the page asks for `after=<cursor>` every 3 s, only while the tab is visible. It stops and calls close when the
conversation leaves the screen.

**Chat rules.** `api/threads` lists only two-way threads: Agent Hub; n8n agents with `chat: true` (on or off); and
Claude sessions whose controls include `send` and `transcript`, on a machine with commands on, that haven't ended.
The page builds the Claude group from the live sessions. A session without a thread opens one (a pointer thread,
`kind: "claude"`, target `<machine>/<session>`). Sending uses the stored-message path → `crew_hub/command send`.
Only what the user sends is stored. The pre-0.2.0 status rows (a session's summary saved when it went idle) are gone.
Crew shows "Can't chat: <reason>" for sessions and agents that aren't two-way (ended, commands off, can't take
messages, older Crew Hub without transcript; for agents the reason comes from `chat_note` or a table in the page).

## 14. Agents by job and OpenCode agents (0.3.0)

Design: design/bridge/categories-opencode (mockup, notes, screenshots), approved by Isaac as shown, with one change:
agents that are off but can chat keep their TALK key (§13 decision 3).

**Agents tab.** Sections in a fixed order from `role`: Engineering (`engineering`), Home (`home`), Security
(`security`), Downloads (`downloads`), Library & media (`library`), Assistants (`general`), Infrastructure
(`service`), Other (any other role, or none), then System (`system`, or no role and a system name) folded. Empty
sections are hidden. The header has a count summary; a failing agent (◆, warn) or one that needs you (▲, caution)
lights the header and the list edge in place, never reordering. Agents whose name starts with `SANDBOX:` never enter
the list or any count (Now included); the System fold says "+N sandbox copies hidden". Talkable rows (`chat: true`,
and every OpenCode agent) carry a TALK key, ◇ for OpenCode, whether the agent is on or off.

**OpenCode agents** are `agent-hub/status` rows with `kind: "opencode agent"`, ids `oc:*`, `role` engineering|home,
`chat: true`, `rerun: false`, `state` ok|running|down and `task` "<title> since …". Their live fields come from n8n:

| n8n endpoint | Answer |
|---|---|
| `GET bridge/opencode/agents` | `{agents: [{id, name, short_name, description, role, busy, busySince, needsApproval, lastSession: {id, title, updated} \| null}]}` |
| `GET bridge/opencode/sessions?agent=&limit=20` | `{sessions: [{id, title, updated, busy, busySince, needsApproval, origin: "bridge" \| "ticket" \| "opencode", ticketId}]}`, newest first; `limit` 1-100 (default 20) |
| `GET bridge/opencode/session?id=` | `{id, agent, title, origin, ticketId, busy, busySince, needsApproval, approvalText, messages: [{role, text, at}], tools: [{name, status: "done" \| "running" \| "pending" \| "error", at}]}` |
| `POST bridge/opencode/message {agent, text ≤ 4000, author, sessionId?}` | `{ok: true, sessionId}`; 409 `{ok: false, error: "agent_busy", sessionId}` (a Bridge chat of this agent is running) or `{ok: false, error: "session_busy", sessionId}`; 400 `{ok: false, error}` (validation, "session <id> not found", "only sessions started from The Bridge can be continued here; …") |

Bridge-started sessions are titled "Bridge: <first 60 characters>"; the page drops the prefix. `approvalText` is a short
machine line ("shell: read /workspace/…", ≤ 300 characters) shown in monospace. Timeouts: agents and sessions 15 s,
session 20 s, message 30 s. A 4xx from these endpoints is an answer, so n8n's health stays green.

**Add-on endpoints** (relative, ingress only):

| Endpoint | Cache | Notes |
|---|---|---|
| `GET api/opencode/agents[?fresh=1]` | 30 s | `{agents, at, cached[, stale, error]}` |
| `GET api/opencode/agents/{id}/sessions[?fresh=1]` | 30 s per agent | 404 `not_found` for a bad or unknown id |
| `GET api/opencode/sessions/{id}[?fresh=1]` | 15 s | Admins only (403 `not_admin`), audited `opencode.session`. Adds `writable` (origin is `bridge`). n8n's "not found" is 404 `not_found` |
| `POST api/opencode/agents/{id}/messages {text, sessionId?, clientId}` | n/a | `@write` (X-Bridge, admin HA user, clientId dedupe). A `sessionId` whose origin isn't `bridge` is refused 403 `not_bridge_session` before n8n is asked. One send per 10 s per user (429 `rate_limited`, `retryAfter`). n8n's busy answers become 409 `agent_busy` / `session_busy` with `sessionId`; other refusals 404 `not_found`, 403 `not_bridge_session` or 409 `refused`. `author` is the HA user's display name. Audit row `opencode.message` (target the agent, summary "new chat" or "session <id>", never the text). SSE `opencode {agent, sessionId}` tells other pages to refresh. |

The caches live in `opencode.py`, in process memory only: shared by every viewer, `fresh` asks n8n again only when the
copy is at least 5 s old, entries go after 120 s, when the last page disconnects and on restart. **Transcripts are never
stored**: nothing from a session reaches SQLite, the snapshot, the SSE stream or the audit (a test greps for a sentinel).
`api/state` carries `opencodeUrl` (the `opencode_url` option, optional).

**Pages.** `#agent/oc:<id>`: description, kv (State, On, Runs in, ID), New chat (or, while a Bridge chat of this agent
runs, "<short name> is busy with another Bridge chat · wait or continue that one" with Continue that chat), a scope box
(talk + sandbox; going live asks for approval in OpenCode; the HA builder adds "Every change to Home Assistant asks for
approval in OpenCode"), Sessions with their origin, "checked every 30 s". `#oc/<agent>/<session|new>`: bubbles, steps as
counts by tool name, "Working… m:ss · you can close the Bridge" from `busySince`, Send off while busy, "Goes to <short
name> as a message from you (sandbox only)". A ticket session shows "Reply in ticket #N" (to the ticket), an
OpenCode-started one "Started in OpenCode · continue there". `needsApproval` shows a caution banner with `approvalText`
and **Open OpenCode ↗** (new tab, `rel=noopener`) when `opencode_url` is set, else "Answer it in OpenCode." The Bridge
has no interrupt and never answers approvals. Chat lists Bridge-origin OpenCode chats as "<name> · OpenCode" (Working… /
▲ Needs your approval); opening one goes to its Crew page. Now's needs-you lists OpenCode agents waiting for approval
after Claude prompts and tickets. Polling, only while the page is visible: agents 30 s (Now, Crew, Chat), the open
agent's sessions 30 s, the open session 15 s.
