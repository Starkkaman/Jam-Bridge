# Changelog

## 0.1.4

- New sidebar icon: the captain's chair (`bridge:captain-chair`), a 24 px Material-style icon from the crew_hub
  integration's icon set (needs crew_hub 0.7.1 or later; without it the sidebar entry has no icon).
- Store icon and logo: the captain's chair redrawn for small sizes (thicker lines, fewer inner lines, the lit button on
  the right armrest in amber).

## 0.1.3

- Tickets support the Engineer (an OpenCode agent on the ticket inbox): new statuses "Needs you" (needs_isaac) and
  "Ready to review" (awaiting_approval) with caution lamps, a Needs you filter, and "Engineer" comments shown as agent
  replies.
- Ready to review: Approve posts "Approved" as you, then reminds you to approve the deploy in OpenCode; Request changes
  sends what should change. Needs you: the comment box is ready for your direction.
- Tickets that need you count in Now's needs-you count and show as Now panels after the Claude prompts.
- The server accepts only the six n8n ticket statuses. "Engineer: notify" and "Engineer: dispatch" are system workflows.

## 0.1.2

- Live updates no longer move the page: the scroll position holds through every re-render (it used to jump to the top
  of the chat while n8n status, crew snapshots and job ticks arrived). The page goes to the top only when you navigate.
- Chat: at the bottom of a thread, new replies keep it pinned there; further up, the position holds and a "New reply ↓"
  chip jumps down.
- Agents n8n reports as `tracked: false` show "Not tracked" instead of a last run (never as a failure); untracked and
  system workflows have no Runs section.

## 0.1.1

- Log lines at info: "listening on … (ingress only)" and "crew_hub websocket connected/disconnected".

## 0.1.0

- First version of The Bridge server: ingress-only aiohttp app with the page API (docs/BRIDGE.md §6), SSE events,
  chats that are kept (90 days), tickets, tasks and runs, the library report with apply (quiet-hours confirm), the
  inbox history, and Claude sessions through crew_hub.
- n8n: allow-listed calls only, the optional `n8n_key` sent as `X-Bridge-Key`, direct chat via `bridge/chat`.
- SQLite in /data (WAL, 0600), audit log of every write, hot backups of a consistent snapshot.
