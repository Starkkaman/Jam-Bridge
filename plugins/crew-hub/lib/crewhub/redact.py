"""Allowlist redaction of a Claude Code hook payload into one small spool record.

Only fields listed here ever leave the hook. Prompts, commands, tool_input as a whole, tool_response bodies, file contents
and agent prompts are never copied. Keep this module import-light: it runs on every hook.
"""

import os
import re

STEP_MAX = 200
SUBJECT_MAX = 120
SUMMARY_MAX = 120
SUMMARY_SCAN = 4000  # only this much of the first line is examined (bounds the regex cost)
MAX_TODOS = 30

FILE_TOOLS = ("Read", "Write", "Edit", "NotebookEdit", "MultiEdit")
AGENT_TOOLS = ("Agent", "Task")
STOP_TOOLS = ("TaskStop", "KillShell", "KillBash")  # KillShell/KillBash: TaskStop's aliases in Claude Code 2.1.310
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,100}$")
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest")


def clip(value, limit):
    """First line of a string, stripped and cut to `limit` characters; None for anything else or empty."""
    if not isinstance(value, str):
        return None
    line = value.strip().split("\n", 1)[0].strip()
    if not line:
        return None
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def clip_text(value, limit):
    """Whitespace collapsed to single spaces, cut to `limit` characters (ending in "…"); None for non-strings/empty.
    crew-hook uses the same function to map clipped question/label text from HA back to the full text."""
    if not isinstance(value, str):
        return None
    line = " ".join(value.split())
    if not line:
        return None
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "\u2026"
    return line


QUESTION_MAX = 200
MAX_QUESTIONS = 4
MAX_OPTIONS = 6


def questions(tool_input):
    """AskUserQuestion questions for the hub: question, header, multiSelect, option labels (each ≤200, max 4×6).
    Option descriptions and anything else in the input are dropped."""
    qs = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(qs, list):
        return None
    out = []
    for q in qs[:MAX_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        text = clip_text(q.get("question"), QUESTION_MAX)
        if not text:
            continue
        item = {"question": text, "multiSelect": q.get("multiSelect") is True, "options": []}
        header = clip_text(q.get("header"), QUESTION_MAX)
        if header:
            item["header"] = header
        opts = q.get("options")
        if isinstance(opts, list):
            for o in opts[:MAX_OPTIONS]:
                label = clip_text(o.get("label"), QUESTION_MAX) if isinstance(o, dict) else None
                if label:
                    item["options"].append(label)
        out.append(item)
    return out or None


def _str(value, limit=200):
    if isinstance(value, str) and value:
        return value[:limit]
    return None


def _hostname(url):
    if not isinstance(url, str):
        return None
    try:
        from urllib.parse import urlsplit  # lazy: only WebFetch pays for it

        host = urlsplit(url.strip()).hostname
    except Exception:
        return None
    return host[:120] if host else None


def _basename(path):
    if not isinstance(path, str) or not path:
        return None
    return os.path.basename(path.rstrip("/"))[:120] or None


_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"'`]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A token starting with / or ~/ that has a further / in it. The lookbehind keeps it from starting mid-word or mid-path.
_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.~/-])(?:~/|/)[^\s/<>\"'`()\[\]{},;]*/[^\s<>\"'`()\[\]{},;]*")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
_HAS_ALPHA = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"[0-9]")


def _url_host(m):
    return _hostname(m.group(0)) or "\u2026"


def _path_base(m):
    return os.path.basename(m.group(0).rstrip("/")) or "\u2026"


def _token(m):
    t = m.group(0)
    return "\u2026" if _HAS_ALPHA.search(t) and _HAS_DIGIT.search(t) else t


def sanitize_summary(value):
    """A Stop/SubagentStop message made safe to publish: first line only, URLs -> hostname, e-mail addresses -> "…",
    absolute/home paths -> basename, token-like runs (20+ chars of [A-Za-z0-9+/=_-] with letters and digits) -> "…",
    then cut to SUMMARY_MAX. None when nothing is left."""
    if not isinstance(value, str):
        return None
    line = value.strip().split("\n", 1)[0].strip()[:SUMMARY_SCAN]
    if not line:
        return None
    if "://" in line:
        line = _URL_RE.sub(_url_host, line)
    if "@" in line:
        line = _EMAIL_RE.sub("\u2026", line)
    if "/" in line:
        line = _PATH_RE.sub(_path_base, line)
    line = _TOKEN_RE.sub(_token, line)
    return clip(line, SUMMARY_MAX)


def tool_fields(tool, tool_input):
    """Safe description of one tool call: a dict of a few short fields, never the raw input."""
    out = {}
    if not isinstance(tool, str):
        return out
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool == "Bash":
        d = clip(ti.get("description"), STEP_MAX)
        if d:
            out["desc"] = d
    elif tool in AGENT_TOOLS:
        d = clip(ti.get("description"), STEP_MAX)
        if d:
            out["desc"] = d
        st = _str(ti.get("subagent_type"), 80)
        if st:
            out["sat"] = st
    elif tool in FILE_TOOLS:
        b = _basename(ti.get("file_path") or ti.get("notebook_path"))
        if b:
            out["file"] = b
    elif tool == "WebFetch":
        h = _hostname(ti.get("url"))
        if h:
            out["host"] = h
    elif tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        if len(parts) == 3:
            out["mcp"] = {"server": parts[1][:80], "tool": parts[2][:80]}
    # WebSearch, Grep, Glob and everything else: the tool name alone.
    return out


def _todos(ti):
    todos = ti.get("todos") if isinstance(ti, dict) else None
    if not isinstance(todos, list):
        return None
    out = []
    for t in todos[:MAX_TODOS]:
        if not isinstance(t, dict):
            continue
        out.append({"content": clip(t.get("content"), SUBJECT_MAX) or "", "status": _str(t.get("status"), 20) or "pending"})
    return out


def plan_path_for(file_path, plans_dir):
    """Return the resolved path when `file_path` is a .json file directly or indirectly under `plans_dir`, else None."""
    if not isinstance(file_path, str) or not file_path.endswith(".json"):
        return None
    try:
        real = os.path.realpath(file_path)
        root = os.path.realpath(plans_dir)
    except Exception:
        return None
    if real.startswith(root + os.sep) and real.endswith(".json"):
        return real
    return None


def redact(payload, ts, pid=None, plans_dir=None, summaries=True):
    """Build the spool record for one hook payload. Never raises for odd input shapes.

    `summaries=False` (config.json "summaries": false) drops the Stop/SubagentStop message entirely."""
    if not isinstance(payload, dict):
        payload = {}
    ev = _str(payload.get("hook_event_name"), 40) or "Unknown"
    rec = {"v": 1, "ts": ts, "ev": ev}
    sid = _str(payload.get("session_id"), 100)
    if sid:
        rec["sid"] = sid
    if pid:
        rec["pid"] = pid
    cwd = _basename(payload.get("cwd"))
    if cwd:
        rec["cwd"] = cwd
    tp = _str(payload.get("transcript_path"), 1024)
    if tp:
        rec["tp"] = tp  # local only: the daemon reads the transcript tail; never published
    if ev == "SubagentStop":
        atp = _str(payload.get("agent_transcript_path"), 1024)
        if atp:
            rec["atp"] = atp  # local only: the daemon sums the agent's token usage from it; never published
    for src, dst, lim in (("agent_id", "agent", 100), ("agent_type", "atype", 80), ("tool_use_id", "tuid", 100)):
        val = _str(payload.get(src), lim)
        if val:
            rec[dst] = val
    eff = payload.get("effort")
    if isinstance(eff, dict):
        lvl = _str(eff.get("level"), 20)
        if lvl:
            rec["eff"] = lvl

    tool = _str(payload.get("tool_name"), 200)
    if tool:
        rec["tool"] = tool
    tool_input = payload.get("tool_input")

    if ev in TOOL_EVENTS and tool:
        rec.update(tool_fields(tool, tool_input))
        if tool == "TodoWrite":
            todos = _todos(tool_input)
            if todos is not None:
                rec["todos"] = todos
        if ev == "PostToolUse":
            resp = payload.get("tool_response")
            if not isinstance(resp, dict):
                resp = {}
            if tool in AGENT_TOOLS:
                aid = _str(resp.get("agentId"), 100)
                if aid:
                    rec["aid"] = aid
                title = clip(resp.get("description"), STEP_MAX)
                if title:
                    rec["title"] = title
                model = _str(resp.get("resolvedModel"), 100)
                if model:
                    rec["model"] = model
                if isinstance(resp.get("isAsync"), bool):
                    rec["async"] = resp["isAsync"]
            elif tool in STOP_TOOLS and isinstance(tool_input, dict):
                # only the stopped task's id (an agentId for background agents; shell_id is the deprecated name)
                tid = tool_input.get("task_id")
                if tid is None:
                    tid = tool_input.get("shell_id")
                if isinstance(tid, str) and TASK_ID_RE.match(tid):
                    rec["stopTask"] = tid
            elif tool == "TaskUpdate":
                tid = resp.get("taskId")
                if tid is None and isinstance(tool_input, dict):
                    tid = tool_input.get("taskId")
                if isinstance(tid, (str, int)):
                    rec["taskId"] = str(tid)[:40]
                sc = resp.get("statusChange")
                if isinstance(sc, dict):
                    rec["statusChange"] = {"from": _str(sc.get("from"), 20), "to": _str(sc.get("to"), 20)}
                elif isinstance(tool_input, dict) and tool_input.get("status") == "deleted":
                    rec["statusChange"] = {"from": None, "to": "deleted"}
                if isinstance(tool_input, dict):
                    subj = clip(tool_input.get("subject"), SUBJECT_MAX)  # a renamed task (same limit as TaskCreated)
                    if subj:
                        rec["subject"] = subj
            elif tool in ("Write", "Edit") and plans_dir and isinstance(tool_input, dict):
                plan = plan_path_for(tool_input.get("file_path"), plans_dir)
                if plan:
                    rec["plan"] = plan

    elif ev in ("TaskCreated", "TaskCompleted"):
        tid = payload.get("task_id")
        if isinstance(tid, (str, int)):
            rec["task_id"] = str(tid)[:40]
        subj = clip(payload.get("task_subject"), SUBJECT_MAX)
        if subj:
            rec["subject"] = subj
    elif ev in ("Stop", "SubagentStop"):
        msg = sanitize_summary(payload.get("last_assistant_message")) if summaries else None
        if msg:
            rec["msg"] = msg
    elif ev == "SessionStart":
        src = _str(payload.get("source"), 40)
        if src:
            rec["source"] = src
        model = payload.get("model")
        if isinstance(model, str):
            rec["model"] = model[:100]
    elif ev == "SessionEnd":
        reason = _str(payload.get("reason"), 40)
        if reason:
            rec["reason"] = reason
    elif ev == "Notification":
        nt = _str(payload.get("notification_type"), 60)
        if nt:
            rec["ntype"] = nt
        msg = clip(payload.get("message"), STEP_MAX)
        if msg:
            rec["msg"] = msg
    elif ev in ("PreCompact", "PostCompact"):
        trig = _str(payload.get("trigger"), 20)
        if trig:
            rec["trigger"] = trig
    return rec


# ---- secret masking (the `transcript` command, docs/COMMANDS.md) --------------------------------------------------
# Compiled on first use only: crew-hook imports this module on every tool call and never masks.
MASK = "\u2022\u2022\u2022\u2022"
_SECRET_RES = None
_KEY_WORDS = (r"(?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key"
              r"|credentials?|session[_-]?key|signing[_-]?key|webhook[_-]?id)")


def _secret_res():
    global _SECRET_RES
    if _SECRET_RES is None:
        _SECRET_RES = (
            # private key blocks (an unterminated block is masked to the end)
            (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S), None),
            # JWTs
            (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), None),
            # well-known API key / token shapes
            (re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
                        r"|glpat-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}"
                        r"|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,})"), None),
            # credentials in URLs: scheme://user:<pass>@host
            (re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(?=@)"), 2),
            # Authorization: Bearer/Basic <value> (the value needs a digit or a token character)
            (re.compile(r"(?i)(\b(?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]*[0-9._~+/=][A-Za-z0-9._~+/=-]*)"
                        r"(?<=[A-Za-z0-9._~+/=-]{8})"), 2),
            # key = value / "key": "value" assignments
            (re.compile(r"(?i)(\b[A-Za-z0-9_.-]*" + _KEY_WORDS + r"[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?)([^\s\"'`,;]{4,})"), 2),
        )
    return _SECRET_RES


_RUN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_RUN = re.compile(r"^[0-9a-fA-F]{32,}$")
ENTROPY_MIN = 4.0  # bits per character, for mixed-case/digit runs
HEX_ENTROPY_MIN = 3.0  # random hex runs of 32+ characters score about 3.5-3.9


def entropy(s):
    """Shannon entropy of a string in bits per character."""
    import math

    if not s:
        return 0.0
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = float(len(s))
    return -sum(c / n * math.log(c / n, 2) for c in counts.values())


def _high_entropy(run):
    if _UUID_RE.match(run):
        return False
    if _HEX_RUN.match(run):
        return entropy(run) >= HEX_ENTROPY_MIN  # hashes, hex secrets and webhook ids (not "aaaa…" or "0000…")
    if not (_HAS_DIGIT.search(run) and re.search(r"[a-z]", run) and re.search(r"[A-Z]", run)):
        return False
    return entropy(run) >= ENTROPY_MIN


def _value_ok(v):
    return not (v.isdigit() and len(v) < 8)  # max_tokens: 4096 is not a secret


def mask_secrets(text):
    """(text with secret-looking parts replaced by "••••", whether anything was masked). Patterns: private-key blocks,
    JWTs, well-known API key shapes, URL credentials, Bearer/Basic values, password/secret/token/api-key assignments
    and 32+ character high-entropy runs."""
    if not isinstance(text, str) or not text:
        return text, False
    hit = [False]

    def whole(m):
        hit[0] = True
        return MASK

    def value(m):
        if not _value_ok(m.group(2)):
            return m.group(0)
        hit[0] = True
        return m.group(1) + MASK

    for rx, group in _secret_res():
        text = rx.sub(value if group else whole, text)

    def run(m):
        r = m.group(0)
        if MASK in r or not _high_entropy(r):
            return r
        hit[0] = True
        return MASK

    text = _RUN_RE.sub(run, text)
    return text, hit[0]
