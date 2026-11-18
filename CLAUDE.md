# The Bridge (was Crew Hub)

Home Assistant hub for live progress of every Claude Code session/agent, with commands back to sessions, shipped as a
Claude Code plugin. Brief: PROMPT.md. Design + verified/assumed table: docs/DESIGN.md. Evidence: docs/evidence/.
Private details: CLAUDE.local.md (local only; hosts, accounts, install state, collaborator sessions, HA facts).

This repo is PUBLIC (HACS installs from it). Tracked files hold no IPs, hostnames, device names, session ids, artifact
URLs, secrets or findings about the home network: use placeholders (`<nas-ip>`, `<ha-host>`, `<machine>`) and keep the
real values in CLAUDE.local.md.

## Status
- Design v1: docs/DESIGN.md. Build step 1: reporter (plugins/crew-hub) + card preview (card/).
- MQTT transport: crew-hubd publishes crew/<machine>/status + crew/<machine>/snapshot (retained), logging in as a
  non-admin, local-only HA user. Config ~/.claude/crew-hub/config.json (0600, no secrets); MQTT password and command
  secret in secrets.json (0600): never read or print it.
- HACS updates: push to GitHub, then update the integration in HA (needs a restart).
- Bridge tests: docs/evidence/bridge-tests.md. Step 5: crew_hub 0.2.0 + plugin 0.2.0, commands per docs/COMMANDS.md.
- 0.3.0: webhook transport for roaming machines (docs/WEBHOOK.md) through the owner's reverse proxy, pairing code shown
  once in HA, `crew-hub-pair` run by the human in their own terminal (never via Claude). Interop test tests/ha/test_interop.py.
- 0.3.1: secrets + webhook ids in a private HA Store (crew_hub.secrets), never in config-entry options; MQTT machines pair by code too.
- 0.4.0: Active plans stack, idle section, stopped agents (restart/inactive/TaskStop), Clear finished + dismiss (HA Store
  crew_hub.dismissed), human names, plan-rule install on pairing.
- 0.4.1: waiting = prompt open now (registry-driven); false "needs you" came from idle_prompt.
- Crew Flow plugin 0.1.0 (plugins/crew-flow, docs/CREW-FLOW.md, rules from docs/research/orchestration-efficiency.md):
  tracking via the built-in task list + agent/task title joins.
- 0.5.1: card layout jumps fixed (stable first-seen order, fixed-line rows/strips, finished agents held in place, scroll
  compensation incl. Safari/HA scroller); measured with card/measure-jumps.js.
- 0.6.0: native Windows machine side (lib/crewhub/osdep.py, docs/WINDOWS.md); POSIX paths unchanged. Card in the Jam look.
- 0.7.0: display name "Crew Hub" → "The Bridge" (docs/BRIDGE.md §10); ids unchanged, entity ids pinned in sensor.py /
  binary_sensor.py so new installs keep `crew_hub_*`. Send prefix `[From Isaac via The Bridge]`, deny `... from The Bridge`.
- Playwright for screenshots: `pw-node script.js`.

## Rules
- Zero-token reporting (hooks only). Never publish tool inputs/outputs, commands, prompts or agent briefs.
- No secrets in git/chat (MQTT passwords, HMAC secrets, webhook ids, ha-mcp path).
- Public repo: real hosts, IPs, names, session ids and network/security facts go in CLAUDE.local.md only. Fixtures and
  tests use made-up names and ids.
- Never retain command topics. LWT on <base>/status. The card uses HA's websocket only.
