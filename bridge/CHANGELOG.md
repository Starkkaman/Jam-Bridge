# Changelog

## 0.3.0

- **Agents by job.** The Agents tab groups agents in a fixed order: Engineering, Home, Security, Downloads, Library &
  media, Assistants, Infrastructure, Other, then System (folded). Empty sections are hidden. Each header has a count
  ("1 working", "2 on · 1 off"); a failing agent or one that needs you lights its section's header and edge in place,
  without reordering. `SANDBOX:` copies are never listed or counted; the System fold says how many are hidden.
- **Talk on the row.** Every agent that can chat gets a small TALK key on its row (◇ marks OpenCode). Agents that are
  off keep it.
- **OpenCode agents** (the Engineer, the HA builder): an agent page with its description, state, a New chat key, what
  it may do from here and its sessions (From the Bridge / From a ticket / Started in OpenCode). A chat page shows the
  replies, its steps as counts ("13 steps · Read 7 · Searched 3 …"), "Working… 2:13 · you can close the Bridge" while it
  works, and a composer that waits until it replies. Only chats started from the Bridge take messages; a ticket chat
  says "Reply in ticket #N", one started in OpenCode says to continue there. One Bridge chat per agent runs at a time:
  a busy agent offers "Continue that chat".
- **Approvals stay in OpenCode.** When an OpenCode agent waits for approval, the Bridge shows it (Now's "needs you",
  the agent, the chat, Chat) with its request and **Open OpenCode ↗**. The Bridge never answers approvals.
- New option `opencode_url` (optional): where Open OpenCode goes. Without it the page says "Answer it in OpenCode".
- Chat lists Bridge-started OpenCode chats as "<agent> · OpenCode", with Working… or ▲ Needs your approval.
- New endpoints: `api/opencode/agents`, `api/opencode/agents/{id}/sessions`, `api/opencode/sessions/{id}` (in memory
  for 15-30 s, shared by every viewer, never stored) and `POST api/opencode/agents/{id}/messages` (one message per 10 s
  per user, audited as `opencode.message`). The page polls them only while it is visible.

## 0.2.2

- A ticket write that n8n rejects with HTTP 500 (its generic "Error in workflow") now reads "n8n refused the change (the
  reason is in n8n's execution log)".
- Log: "crew_hub websocket connected" no longer prints a machine count read before the first snapshot. That count
  said 0 at every start. The count is now logged when the snapshot arrives ("crew_hub: N machines").
- DOCS: a 403 from n8n can also mean the Bridge's address isn't on n8n's allow-list.

## 0.2.1

- Fix: ticket status changes, comments and Approve now go to n8n's ticket-update endpoint. Before, they went to the
  create endpoint, which refused them as an empty ticket.
- When n8n refuses a ticket write, the Bridge shows n8n's reason (for example "n8n: no such ticket"), and n8n's health
  stays green, since n8n itself is fine.

## 0.2.0

- Agent output: an agent's page shows its **Latest output** (the newest run's summary) above its Runs, in plain words
  (Scheduled, From a message, From the Bridge). A run's page shows its summary, **What it did** (each tool and its call
  count, failures marked), the last step and its output. A failed run shows n8n's error and, where the agent allows it,
  **Try again**.
- Services show their **Report** (24 lines, then Show all), when it was updated, and Refresh.
- New endpoints: `api/agents/{id}/runs/{runId}` (cached 60 s; untracked and system agents have none) and
  `api/agents/{id}/output` (cached 30 s; `?fresh=1` asks n8n again).
- **Talk** opens a direct chat titled with the agent's name, its role and DIRECT. It also works for agents that are off,
  as long as they have chat. Agents that can't chat say why ("Can't chat: …").
- Claude sessions get a **Conversation** page (`#session/<machine>/<id>`) with a two-way composer. The conversation
  comes from the machine through `crew_hub/transcript` when you open it. It is kept in memory for 60 s at most and
  never written to the database. Steps show as counts per reply ("Read 3 files · Ran 2 commands"), and masked secrets
  show as •••• MASKED.
- Chat lists only two-way threads: Agent Hub, agents with chat, and Claude sessions that can take messages and send
  their conversation. The one-way "Claude · message only" threads are gone. A Claude thread polls for new turns every
  3 s while it is open.
- Every conversation open, poll and refresh, and every run and output fetch, is in the audit log with the HA user.

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
