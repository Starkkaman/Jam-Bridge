"""One session's recent conversation for the `transcript` command (docs/COMMANDS.md). Read on demand, never cached.

Source: the session transcript (<projects>/<cwd key>/<session id>.jsonl, the `tp` the hooks record), read from the end
(at most TAIL_MAX bytes). Turns, oldest first:
- user: real prompts only. Skipped: tool results, meta/hook/system entries, compaction summaries, sidechains, slash
  command and local-command output, task notifications, interruption markers and messages from other sessions. A
  message sent from The Bridge (a peer message from crew-hub) is kept with its "[From ... via The Bridge]" prefix.
- assistant: text blocks; the tool calls since the previous text become counts by verb (no inputs, no outputs).
- report: a subagent's final report: its SubagentHandback message, or the last assistant message of its own transcript
  (<session transcript minus .jsonl>/subagents/agent-<id>.jsonl) once it finished.

Cursor: "1.<offset>.<at>": the byte offset just past the newest turn read from the session transcript, and the newest
turn time (ms; filters reports read from subagent transcripts). `after` returns only newer turns.
Every text is masked (redact.mask_secrets) and clipped; the whole plaintext stays under PLAIN_MAX.
"""

import json
import os
import re
from datetime import datetime

from . import paths, redact

TAIL_MAX = 2 * 1024 * 1024  # most bytes read from the session transcript
AGENT_TAIL = 256 * 1024  # most bytes read from one subagent transcript
REPORT_AGENTS_MAX = 20  # most subagent transcripts read per request
TEXT_MAX = 4000
TITLE_MAX = 200
AGENT_MAX = 80
MASK_SCAN = TEXT_MAX + 512  # text examined by the masker (the rest is clipped anyway)
PLAIN_MAX = 47 * 1024  # contract: <= 48 KB; base64 of this plus the ack envelope stays under 64 KiB
LIMIT_DEFAULT = 20
LIMIT_MAX = 40

CURSOR_RE = re.compile(r"^1\.(\d{1,15})\.(\d{1,15})$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_TAG_RE = re.compile(r"^\s*<([A-Za-z][A-Za-z0-9_-]*)[\s>]")
_REMINDER_RE = re.compile(r"<system-reminder>.*?(?:</system-reminder>|\Z)", re.S)
SKIP_TAGS = frozenset((
    "command-name", "command-message", "command-args", "command-contents", "local-command-stdout",
    "local-command-stderr", "local-command-caveat", "bash-input", "bash-stdout", "bash-stderr", "task-notification",
    "system-reminder", "user-prompt-submit-hook", "cross-session-message", "teammate-message", "task-reminder",
))
SKIP_PREFIXES = ("[Request interrupted by user", "Caveat: The messages below were generated")
BRIDGE_FROM = "crew-hub"

VERBS = {
    "Read": "read",
    "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "Write": "write",
    "Grep": "search", "Glob": "search", "ToolSearch": "search",
    "Bash": "run", "PowerShell": "run",
    "WebFetch": "web", "WebSearch": "web",
    "Agent": "agent", "Task": "agent",
}


class Unavailable(Exception):
    """args[0]: "transcript_unavailable" | "too_large"."""


def verb(tool):
    return VERBS.get(tool, "other")


def parse_cursor(value):
    """(offset, at) of a cursor string, or None when it isn't one."""
    m = CURSOR_RE.match(value) if isinstance(value, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def make_cursor(offset, at):
    return "1.%d.%d" % (max(0, int(offset)), max(0, int(at or 0)))


def iso_ms(ts):
    """Epoch ms of a transcript timestamp ("2026-11-18T10:00:00.123Z"), or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
        if m and m.group(2):  # Python 3.9 takes only 3 or 6 fraction digits
            s = m.group(1) + (m.group(2) + "000000")[:7] + m.group(3)
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            return None
        return int(d.timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


# ---- locating ----------------------------------------------------------------------------------------------------
def project_key(cwd):
    """Claude Code's folder name for a working directory: every character other than [A-Za-z0-9] becomes "-"
    (POSIX "/home/me/p" -> "-home-me-p", Windows "C:\\Users\\me\\p" -> "C--Users-me-p")."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _is_file(path):
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def find_transcript(sid, tp=None, cwd=None, projects=None):
    """Path of the session's transcript: the hook-recorded `tp`, else <projects>/<key(cwd)>/<sid>.jsonl, else any
    <projects>/*/<sid>.jsonl (long folder names are shortened by Claude Code). None when there is none."""
    if not isinstance(sid, str) or not ID_RE.match(sid):
        return None
    name = sid + ".jsonl"
    if isinstance(tp, str) and tp.endswith(".jsonl") and os.path.basename(tp) == name and _is_file(tp):
        return tp
    projects = projects or paths.projects_dir()
    if isinstance(cwd, str) and cwd:
        p = os.path.join(projects, project_key(cwd), name)
        if _is_file(p):
            return p
    try:
        folders = sorted(os.listdir(projects))
    except OSError:
        return None
    for d in folders:
        p = os.path.join(projects, d, name)
        if _is_file(p):
            return p
    return None


# ---- reading -----------------------------------------------------------------------------------------------------
def read_window(path, start=None, tail=None):
    """(base offset, bytes of whole lines, gap) from `start` (a line start) or the last `tail` bytes. `gap` is True
    when older content was skipped. Raises Unavailable."""
    tail = tail or TAIL_MAX
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            cut = False
            gap = False
            if start is None or start > size:
                begin = max(0, size - tail)
                cut = gap = begin > 0
            elif size - start > tail:
                begin = size - tail
                cut = gap = True
            else:
                begin = start
            f.seek(begin)
            data = f.read(size - begin)
    except OSError:
        raise Unavailable("transcript_unavailable")
    base = begin
    if cut:
        i = data.find(b"\n")
        if i < 0 or i == len(data) - 1:
            raise Unavailable("too_large")  # the last line alone is longer than the whole window
        base += i + 1
        data = data[i + 1:]
    end = data.rfind(b"\n")
    data = data[: end + 1] if end >= 0 else b""  # a last line still being written is left for later
    return base, data, gap


def _texts(content):
    """Text blocks of a user message (str or list content); None when it carries a tool result."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_result":
            return None
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(parts)


def user_text(d):
    """The prompt text of a transcript entry when it is a real user prompt (or a Bridge message), else None."""
    if d.get("type") != "user" or d.get("isSidechain") or d.get("isCompactSummary") or d.get("isVisibleInTranscriptOnly"):
        return None
    msg = d.get("message")
    if not isinstance(msg, dict) or d.get("toolUseResult") is not None:
        return None
    text = _texts(msg.get("content"))
    if not isinstance(text, str):
        return None
    origin = d.get("origin")
    okind = origin.get("kind") if isinstance(origin, dict) else origin
    if okind == "peer" and isinstance(origin, dict) and origin.get("from") == BRIDGE_FROM:
        i = text.find("[From ")
        text = text[i:] if i >= 0 else text.split("\n", 1)[-1]
    elif d.get("isMeta") or okind not in (None, "human"):
        return None
    text = _REMINDER_RE.sub("", text).strip()
    if not text or text.startswith(SKIP_PREFIXES):
        return None
    m = _TAG_RE.match(text)
    if m and m.group(1) in SKIP_TAGS:
        return None
    return text


def _handback(d):
    """(agent id, report text) of a SubagentHandback delivery entry, else None."""
    origin = d.get("origin")
    if d.get("type") != "user" or not isinstance(origin, dict) or origin.get("kind") != "peer" or not origin.get("handback"):
        return None
    aid, body = origin.get("from"), origin.get("body")
    if not isinstance(body, str) or not body.strip():
        body = origin.get("handbackReportText")
    if not isinstance(aid, str) or not ID_RE.match(aid) or not isinstance(body, str) or not body.strip():
        return None
    return aid, body.strip()


def parse(data, base):
    """Turns from whole JSONL lines starting at offset `base`. Returns (turns, agents) where turns carry internal
    keys "_end" (offset past their line) and, for reports, "_aid"; agents maps agent id -> {"type", "title"} from the
    Agent/Task calls seen."""
    turns = []
    pending = {}  # verb -> count since the previous assistant text
    pending_at = pending_end = None
    cur = None  # assistant turn that later text blocks of the same message join
    calls = {}  # tool_use id -> (subagent type, description)
    agents = {}
    last_at = 0
    off = base
    lines = data.split(b"\n")
    for raw in lines[:-1] if lines and lines[-1] == b"" else lines:
        end = off + len(raw) + 1
        off = end
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        at = iso_ms(d.get("timestamp")) or last_at
        last_at = max(last_at, at)
        typ = d.get("type")
        if typ == "assistant" and not d.get("isSidechain"):
            msg = d.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            mid = msg.get("id")
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and isinstance(b.get("text"), str) and b["text"].strip():
                    if cur is not None and mid and cur["_mid"] == mid:
                        cur["text"] += "\n\n" + b["text"].strip()
                        cur["_end"] = end
                    else:
                        cur = {"kind": "assistant", "at": at, "text": b["text"].strip(), "steps": pending,
                               "_end": end, "_mid": mid}
                        turns.append(cur)
                        pending, pending_at, pending_end = {}, None, None
                elif bt == "tool_use":
                    name = b.get("name") if isinstance(b.get("name"), str) else ""
                    v = verb(name)
                    pending[v] = pending.get(v, 0) + 1
                    pending_at, pending_end = at, end
                    cur = None
                    if v == "agent" and isinstance(b.get("id"), str):
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        calls[b["id"]] = (inp.get("subagent_type"), inp.get("description"))
            continue
        if typ != "user":
            continue
        res = d.get("toolUseResult")
        if isinstance(res, dict) and isinstance(res.get("agentId"), str) and ID_RE.match(res["agentId"]):
            msg = d.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("tool_use_id") in calls:
                    st, desc = calls[b["tool_use_id"]]
                    agents[res["agentId"]] = {"type": st, "title": res.get("description") or desc}
            continue
        hb = _handback(d)
        if hb:
            turns.append({"kind": "report", "at": at, "agent": None, "title": None, "text": hb[1], "_aid": hb[0],
                          "_end": end})
            continue
        text = user_text(d)
        if text is None:
            continue
        if pending:  # tool calls without a closing text (e.g. interrupted): kept as a steps-only turn
            turns.append({"kind": "assistant", "at": pending_at, "text": "", "steps": pending, "_end": pending_end})
            pending, pending_at, pending_end = {}, None, None
        cur = None
        turns.append({"kind": "user", "at": at, "text": text, "_end": end})
    for t in turns:
        t.pop("_mid", None)
    return turns, agents


def _meta(agent_tp):
    """The subagent's .meta.json next to its transcript ({} when missing)."""
    try:
        with open(agent_tp[: -len(".jsonl")] + ".meta.json", "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def last_assistant(path, tail=None):
    """(at ms, text) of the last assistant message with text in a (subagent) transcript, or None."""
    try:
        base, data, _ = read_window(path, None, tail or AGENT_TAIL)
    except Unavailable:
        return None
    mid = None
    parts = []
    at = None
    for raw in reversed(data.split(b"\n")):
        if b'"assistant"' not in raw:
            if parts and raw.strip() and b'"user"' in raw:
                break  # a user entry ends the message
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
            continue
        msg = d["message"]
        if parts and msg.get("id") != mid:
            break
        texts = [b["text"].strip() for b in msg.get("content") or []
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip()]
        if not texts:
            continue
        if not parts:
            mid = msg.get("id")
            at = iso_ms(d.get("timestamp"))
            if not mid:
                parts = texts
                break
        parts[:0] = texts
    if not parts:
        return None
    return at, "\n\n".join(parts)


def agent_path(tp, aid):
    if not (isinstance(tp, str) and tp.endswith(".jsonl") and isinstance(aid, str) and ID_RE.match(aid)):
        return None
    return os.path.join(tp[: -len(".jsonl")], "subagents", "agent-%s.jsonl" % aid)


def _file_reports(tp, seen, known, model_agents):
    """Reports from finished subagents' own transcripts, for agents seen in the window or known to the daemon."""
    order = []
    for aid in list(seen) + [a.get("id") for a in model_agents]:
        if isinstance(aid, str) and ID_RE.match(aid) and aid not in order:
            order.append(aid)
    by_id = {a.get("id"): a for a in model_agents if isinstance(a, dict)}
    out = []
    for aid in order[-REPORT_AGENTS_MAX:]:
        if aid in known:
            continue
        ma = by_id.get(aid) or {}
        atp = ma.get("atp") if isinstance(ma.get("atp"), str) and ma["atp"].endswith(".jsonl") else agent_path(tp, aid)
        if not atp:
            continue
        meta = _meta(atp)
        if ma.get("status") != "done" and meta.get("finished") is not True:
            continue
        last = last_assistant(atp)
        if not last:
            continue
        at, text = last
        out.append({"kind": "report", "at": at or ma.get("endedAt") or 0, "agent": None, "title": None, "text": text,
                    "_aid": aid})
    return out


def _fill_agent(t, info, model_agents, tp):
    aid = t.pop("_aid", None)
    a = next((x for x in model_agents if isinstance(x, dict) and x.get("id") == aid), None) or {}
    w = info.get(aid) or {}
    meta = _meta(agent_path(tp, aid)) if aid and agent_path(tp, aid) else {}
    t["agent"] = redact.clip_text(meta.get("agentType") or a.get("type") or w.get("type"), AGENT_MAX) or "agent"
    t["title"] = redact.clip_text(meta.get("description") or a.get("title") or w.get("title"), TITLE_MAX) or ""


def _finish_text(text):
    masked_text, masked = redact.mask_secrets(text[:MASK_SCAN])
    if len(masked_text) > TEXT_MAX:
        masked_text = masked_text[: TEXT_MAX - 1].rstrip() + "\u2026"
    return masked_text, masked


def _public(t):
    out = {k: v for k, v in t.items() if not k.startswith("_")}
    masked = False
    for key in ("text", "title"):
        if isinstance(out.get(key), str) and out[key]:
            out[key], m = _finish_text(out[key])
            masked = masked or m
    if masked:
        out["masked"] = True
    if out["kind"] == "assistant":
        out["steps"] = dict(out.get("steps") or {})
    out["at"] = int(out.get("at") or 0)
    return out


def _size(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def read(sid, path, after=None, limit=LIMIT_DEFAULT, agents=None):
    """The plaintext object of the contract for the transcript at `path`. `agents`: the daemon's agents of this
    session ([{id, type, title, status, endedAt, atp}]). Raises Unavailable."""
    agents = [a for a in (agents or []) if isinstance(a, dict)]
    limit = max(1, min(LIMIT_MAX, int(limit or LIMIT_DEFAULT)))
    cur = parse_cursor(after) if after is not None else None
    try:
        size = os.path.getsize(path)
    except OSError:
        raise Unavailable("transcript_unavailable")
    if cur and cur[0] > size:
        cur = None  # not a position in this file (rewritten): start over from the end
    base, data, gap = read_window(path, cur[0] if cur else None)
    main, info = parse(data, base)
    known = set(t["_aid"] for t in main if t["kind"] == "report")
    seen = list(info)
    reports = _file_reports(path, seen, known, agents)
    if cur:
        reports = [r for r in reports if r["at"] > cur[1]]
    elif main:
        first = min(t["at"] or 0 for t in main)
        reports = [r for r in reports if r["at"] >= first]
    combined = sorted(main + reports, key=lambda t: t["at"] or 0)
    more = gap or len(combined) > limit
    sel = combined[-limit:]
    offset = max([t["_end"] for t in main] or [cur[0] if cur else base])
    newest = max([t["at"] or 0 for t in combined] + [cur[1] if cur else 0])
    for t in sel:
        if t["kind"] == "report":
            _fill_agent(t, info, agents, path)
    turns = [_public(t) for t in sel]
    out = {"session": sid, "cursor": make_cursor(offset, newest), "more": more, "turns": []}
    room = PLAIN_MAX - _size(dict(out, more=True))
    sizes = [_size(t) + 1 for t in turns]
    while turns and sum(sizes) > room:
        turns.pop(0)
        sizes.pop(0)
        more = True
    if sel and not turns:
        raise Unavailable("too_large")
    out["turns"] = turns
    out["more"] = more
    return out
