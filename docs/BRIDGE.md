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
| 4 | Does outbound traffic from the container reach <nas-ip> with the HA host's IP? | **Yes by construction, not yet seen live.** Apps sit on the Docker bridge `hassio` (172.30.32.0/23). It is created with only a bridge-name option, so Docker's default IP masquerade applies. Packets leave with the address of the host interface that routes to the NAS subnet: that is <ha-host-ip> if it is the host's only interface. Not testable from here. On first run, check that n8n's allow-list accepts the calls (§7, `n8n.firstContact`). | `docker/network.py:32-50`, `const.py:69` (`DOCKER_IPV4_NETWORK_MASK` 172.30.32.0/23); Docker bridge driver docs (`enable_ip_masquerade` defaults to true). |
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
 │ n8n.py  ── HTTP, NAT'd to the HA host IP ──► n8n <nas-ip>:<n8n-port> /webhook/agent-hub/* and /webhook/bridge/* │
 │ jobs.py    chat sender, library apply, pollers          db.py ── SQLite /data/bridge.db (WAL)                     │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
crew_hub (the HACS integration) keeps MQTT/webhook transport, HMAC signing and the Lovelace card; the app only uses it.

## 3. Repo layout and packaging
```
repository.yaml   name "JamBoard", url https://github.com/Starkkaman/JamBoard, maintainer "Isaac" (HACS ignores it)
bridge/           config.yaml Dockerfile run.sh (LF) CHANGELOG.md DOCS.md icon.png logo.png translations/en.yaml
  app/            server.py api.py crew.py n8n.py jobs.py db.py backup.py  static/ (index.html app.css app.js favicon.svg fonts/)
tests/bridge/     test_*.py fake_n8n.py fake_core_ws.py ingress_proxy.py  fixtures/n8n/ (moved here from bridge/fixtures/)
```
- `config.yaml`: `name: "The Bridge"`, `version: "0.1.0"`, `slug: bridge` (permanent: it names the data folder), `arch: [amd64,
  aarch64]`, `init: false`, `startup: application`, `ingress: true`, `ingress_port: 8099`, `panel_title: "The Bridge"`,
  `panel_icon: "mdi:bridge"`, `panel_admin: true` (written out), `homeassistant_api: true`, `backup: hot`, `backup_pre: "python3
  /app/backup.py"`, `backup_exclude: [bridge.db, bridge.db-wal, bridge.db-shm]`, `options/schema: n8n_url (url, default
  http://<nas-ip>:<n8n-port>)`. No `ports`, `hassio_api`, `map` or host network.
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
- **Allow-list by construction** (like Callsheet's `ha.py`): only the nine (method, path) pairs of the NAS contract under `n8n_url`.
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
would orphan threads)? 3. Can `bridge/inbox` take `?after=<id>`? 4. Which source IP does the allow-list see from the HA host (we
expect <ha-host-ip>, never 172.30.32.0/23)?

**Mr Manager:** is the installed Supervisor ≥ 2026.11.0 (server-side `panel_admin`, Groups header)? Then add the repository
https://github.com/Starkkaman/JamBoard to the app store.
