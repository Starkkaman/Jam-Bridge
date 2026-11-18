# Changelog

## 0.1.1

- Log lines at info: "listening on … (ingress only)" and "crew_hub websocket connected/disconnected".

## 0.1.0

- First version of The Bridge server: ingress-only aiohttp app with the page API (docs/BRIDGE.md §6), SSE events,
  chats that are kept (90 days), tickets, tasks and runs, the library report with apply (quiet-hours confirm), the
  inbox history, and Claude sessions through crew_hub.
- n8n: allow-listed calls only, the optional `n8n_key` sent as `X-Bridge-Key`, direct chat via `bridge/chat`.
- SQLite in /data (WAL, 0600), audit log of every write, hot backups of a consistent snapshot.
