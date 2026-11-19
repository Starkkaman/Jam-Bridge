# The Bridge

Your Claude Code sessions (the `crew_hub` integration) and the n8n agent crew in one Home Assistant page: Overview, Crew, Chat, Tickets,
Tasks, Library and Inbox. Chats are kept on the Bridge, so a reply that arrives while the page is closed is waiting
when you come back.

## Before you start

- The `crew_hub` integration (shown as **The Bridge** in Home Assistant, from this same repository via HACS) must be installed: The Bridge reads
  your Claude sessions through it.
- Home Assistant Supervisor 2026.11.0 or newer (admin-only panel enforced by the Supervisor, user groups header).
- Your n8n instance must accept calls from the Home Assistant host (the add-on's traffic leaves with the host's
  LAN address).

## Options

| Option | Required | What it is |
|---|---|---|
| `n8n_url` | yes | Base URL of n8n, e.g. `http://<nas-ip>:<n8n-port>`. The Bridge only calls its fixed list of `/webhook/agent-hub/*` and `/webhook/bridge/*` endpoints under it. |
| `n8n_key` | no (until n8n asks for it) | Shared secret sent as the `X-Bridge-Key` header on every n8n call. It stays in the app options: never shown in the page, never logged, never stored in the Bridge's database. If n8n answers 401/403, the page shows "n8n key missing or wrong". |
| `log_level` | no | `debug`, `info` (default), `warning` or `error`. |

## Who can use it

- The panel is admin-only (`panel_admin`): Home Assistant refuses non-admin users before a request reaches the app.
- The app listens only for Home Assistant's ingress proxy. Every change (a chat message, a ticket, a library apply,
  a Claude command) is made in the name of the Home Assistant user who made it and written to an audit log.
- Destructive steps need a confirm: library Apply (a second confirm to apply during the 17:00-01:00 quiet hours),
  Claude Stop and Interrupt.

## Data

- Everything lives in the app's private `/data` folder (`bridge.db`, SQLite).
- Chat messages are kept for **90 days** (pruned nightly); the inbox history is kept.
- The app is included in Home Assistant backups (a consistent snapshot of the database is taken just before each
  backup). Backups contain your chat text, so use HA's backup encryption.
- Agent output, tickets and inbox items are shown as plain text; the Bridge never acts on what an agent writes.

## Troubleshooting

- **"n8n unreachable since …"**: the last good data stays on screen; check `n8n_url` and that n8n accepts the HA
  host. The log shows `n8n.firstContact HTTP <status>` once after each start.
- **"n8n key missing or wrong"**: paste the key n8n expects into `n8n_key` and restart the app. n8n answers 403 the same way when the Bridge's address is not on its allow-list, so if the key is right, check that next.
- **Claude sessions missing**: check that the `crew_hub` integration is set up; the Bridge reconnects to Home
  Assistant on its own after a restart.
