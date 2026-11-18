# The Bridge: mockup notes (2026-11-18)

Open `mockup.html` offline. The harness bar switches Mode (stands in for HA's theme), Width, Scene, Clock, Precheck and Library. `?shot=1` hides the harness and skips the lamp test.
URL options are listed in the file's header comment. The look is Mission Control · Phosphor round 3, synced to jam-design-mc 15e7096: the mc-r3 shell, the mc.css parts (copied) and the token file.
The tokens sit in one block at the top of the CSS. Night is the default. Auto mode follows `?theme=` or `html[data-theme]`, then the device. PACE (`--pace`, 1) multiplies every duration.

## Design decisions
- **Shell:** a band with the name and an amber clock; desktop adds an n8n link lamp and "Updated 2 s ago". Phone: 5 tabs (Now · Crew · Chat · Tickets · More); Tasks, Library and Inbox live under More, and back is "‹ Parent" in the band. Desktop (960 px and up): a rail with all 7 places, current = amber left rule, plus a crumb.
- **Now (dark cockpit):** one mono line ("2 need you.") and a dim summary, then needs-you panels with the request in a readout well. Then the status board (sessions + 7 agents + one System cell), a Running now log well, and Machines. Fine = dark cell + green square + dim word; only "needs you" (amber outline ▲) and failures (red ◆) light up.
- **One lit key per screen:** Allow on the oldest answerable prompt (Now, Crew), Send (Chat), New ticket on the empty list, File ticket, Add comment, Apply all safe outside quiet hours. During an alert, its red Try again is the only filled key and Allow goes unlit. Tasks, Inbox, More, Agents and quiet-hours Library have no lit key.
- **Guarded dialog with a poll:** used for Claude Stop and for Library apply in quiet hours (17:00–01:00). The first tap opens it and runs the checks (GO lines 220 ms apart); the action lights only when all are GO; the second tap acts. "Applying…" shows a 20-segment progress with a blinking edge and "Done about hh:mm".
- **Counts:** a count waiting for an answer is an amber outline (`.count.caut`); it is filled red only while something has failed.
- **Feedback:** failures that need action are banners (Download Daily Summary failed; "No reply after 2 min" in Chat). Confirmations are one toast readout line that never stacks.
- **Chat (Open WebUI layout):** list grouped by day plates plus "Claude · message only"; the thread view has tool-step lamp lines and code in wells. "Thinking… 0:24" uses a busy square and the line "Replies take 10–60 s, sometimes 2 min. You can close the Bridge; the reply waits here." Chats are kept on the Bridge for 90 days.
- **Motion:** lamp test on first open per session, a 180 ms fade on navigation only, and a busy blink of 1 s steps. Reduced motion makes everything instant.

## Crew Hub signatures kept (restyled)
- The session panel as a whole: state tag, summary, ask, plan, agent thread, meta, Message / More, idle fold.
- The ask block becomes a readout well with a tool plate. Its forms: Allow / Deny / Add a note; option keys for questions; the past form "Allowed 16:16".
- The segmented plan track keeps one segment per task (done = amber, running = text edge, failed = red).
- The agent thread spine uses square nodes: busy blink, done, ◆ failed, hollow stopped.
- The context bar becomes a 20-segment CTX meter, with ▲ caution at 85% and above.
- Interrupt uses two taps. Stop is now a guarded key with a poll.

## Data
- Real fixtures, inlined verbatim: the card's snapshot-sample.json plus bridge/fixtures/n8n/*.json. Inbox text and all chat threads are invented samples.
- **Clock:** frozen at status.updated + 2 s (16:16 UTC = UK). The Claude snapshot is moved by the same delta. `clock=quiet` moves everything to 19:30; the library report keeps its own time.
- **Library demo:** findings 14–25 are shown as not applied, to match the inbox's "12 fixes". The fixture has all 25 applied (`lib=fixture`).
- **Library size:** headers show the true total (128, from the agent's status); the library.json fixture is cut to 25 findings.
- **Quiet-hours poll:** reads GET /bridge/library/precheck. The lines are Library agent online (from /status), Nothing playing on Jellyfin, and Old files kept for undo. If something is playing, the line becomes ▲ "1 playing: <title>" and stays allowed, because Apply skips what's playing. If Jellyfin isn't reached, the line becomes ▲ "Couldn't check Jellyfin". A NO-GO keeps the action off. library-poll shows "1 playing: Inside Out"; `?pre=clear|unreachable` shows the others.
- **Apply all safe** = severity low + replace_trailer (confirmed by Isaac).
- **Offline machines** stay quiet (dashed Offline) unless they missed a scheduled task; then it's a ◆ alert. Scene "Mac missed runs": "Missed 2 scheduled runs since 15:38" on Now, fix key Open Crew, lit only when it's the only alert. Shot: now-missed-night-400.
- **Settings:** phone More → Settings; desktop rail footer. Theme Follow HA (default) / Night / Day as a segmented control (amber edge = selected), plus "Chat history: kept 90 days" as plain text. Shot: settings-night-400.
- **Runs:** runs.json has no workflow id, so its runs are shown as Download Daily Summary's (run 140 = its last_run). Other workflows show one row built from last_run. Services have no runs.
- **Tickets:** `agent_id` is used when present. The fixture predates it, so the name "Jellyfin library agent" is mapped to svc:media-agent. New tickets store both. Comments by "NAS agent" are agent replies; the rest are Isaac's.

## Deviations from the kit (for the Jam-Design Lead)
- Now in the kit (15e7096) and copied from it: --fln field edges, the 600 nameplate, `.count.caut`, and the quieter busy edge (busy mixed 50% into the line colour).
- Day amber #8a4f00 on the day header #ddd5c4 is 4.497:1. The band clock is mixed 10% toward text.
- The phone tab bar has 5 tabs; mc-r3's phone example shows 4.

## Checks (verify.js via pw-node, 28 shots)
No console errors, no outside requests, no horizontal overflow, all text ≥ 12 px, phone targets ≥ 44 px, and text contrast ≥ AA. Each screen has at most one lit or fix key, and none lit next to a fix. Board lamps are not counted as keys; the page behind a dialog is inert.

## Open questions for Isaac
1. While something has failed, Allow loses its lit fill (alert first). Is that OK?
