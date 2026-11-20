"""n8n agent helpers: grouping (agents / services / system) and the loose ticket-agent name match (docs/BRIDGE.md §7)."""

import re

SYSTEM_PREFIXES = ("Agent Hub: ",)
SYSTEM_NAMES = frozenset({"The Bridge: API", "Phone Reply Agent", "Engineer: notify", "Engineer: dispatch"})
STOP_WORDS = frozenset({"agent", "the", "a"})
AGENT_ID_RE = re.compile(r"^(n8n|svc|claude):[\w:.-]{1,80}$")


# Agents tab sections (docs/BRIDGE.md §14), in this order; any other role (or none) is "other"; "system" is last, folded
JOBS = ("engineering", "home", "security", "downloads", "library", "general", "service")
SANDBOX_RE = re.compile(r"^SANDBOX:", re.I)


def is_sandbox(agent):
    return bool(SANDBOX_RE.match(str((agent or {}).get("name") or "")))


def job_of(agent):
    role = agent.get("role")
    if role == "system" or (not role and group_of(agent) == "system"):
        return "system"
    return role if role in JOBS else "other"


def group_of(agent):
    name = agent.get("name") or ""
    if name in SYSTEM_NAMES or name.startswith(SYSTEM_PREFIXES):
        return "system"
    if str(agent.get("id") or "").startswith("svc:"):
        return "services"
    return "agents"


# "Try again": n8n marks agents `rerun: true`; `rerun_via` names another endpoint for services. Only these are used.
RERUN_VIA = {"bridge/library/scan": "scan"}
SCAN_HINT = "This starts a library scan now (read-only: nothing is changed)."


def bare_workflow_id(value):
    v = str(value or "")
    return v[4:] if v.startswith("n8n:") else v


def rerun_plan(agent):
    """("scan" | "run", hint) for an agent that n8n marks re-runnable, else None."""
    if not isinstance(agent, dict) or agent.get("rerun") is not True:
        return None
    via = agent.get("rerun_via")
    if via:
        kind = RERUN_VIA.get(via)
        return (kind, SCAN_HINT) if kind else None
    if not agent.get("workflowId"):
        return None
    name = agent.get("name") or "this workflow"
    return "run", f"This runs {name} again now, and it sends its notification to your phone again."


def grouped(status):
    """agents / services / system, each agent with its `job` section; SANDBOX:* copies are left out (only counted)."""
    out = {"agents": [], "services": [], "system": []}
    for a in (status or {}).get("agents") or []:
        if isinstance(a, dict) and a.get("id") and not is_sandbox(a):
            g = group_of(a)
            plan = rerun_plan(a)
            out[g].append({**a, "group": g, "job": job_of(a), "hasRuns": g != "system" and bool(a.get("workflowId")),
                           "canRerun": plan is not None, "rerunHint": plan[1] if plan else None})
    return out


def sandbox_hidden(status):
    return sum(1 for a in (status or {}).get("agents") or [] if isinstance(a, dict) and is_sandbox(a))


def find_agent(status, agent_id):
    for a in (status or {}).get("agents") or []:
        if isinstance(a, dict) and a.get("id") == agent_id:
            return a
    return None


def words(name):
    return frozenset(w for w in re.findall(r"[a-z0-9]+", (name or "").casefold()) if w not in STOP_WORDS)


def match_agent(name, agents):
    """Free-text ticket `agent` -> agent id, or None ("Other"). Equal word sets win over containment; an ambiguous
    containment stays unmatched."""
    w = words(name)
    if not w:
        return None
    equal, contained = [], []
    for a in agents or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        aw = words(a.get("name"))
        if not aw:
            continue
        if aw == w:
            equal.append(a["id"])
        elif w <= aw or aw <= w:
            contained.append(a["id"])
    if len(equal) == 1:
        return equal[0]
    if not equal and len(contained) == 1:
        return contained[0]
    return None
