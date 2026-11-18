"""Settings from the add-on options (/data/options.json, written by the Supervisor) plus environment overrides for
local runs and tests. The n8n key lives only in this object (repr hides it); it is never logged, stored or returned."""

import ipaddress
import json
import logging
import os
import pathlib
import urllib.parse
from dataclasses import dataclass, field

LOG = logging.getLogger("bridge")

SUPERVISOR_IP = "172.30.32.2"
DEFAULT_ALLOW = (SUPERVISOR_IP, "127.0.0.1", "::1")
CORE_WS = "ws://supervisor/core/websocket"
LOG_LEVELS = ("debug", "info", "warning", "error")


def parse_ips(spec):
    out = set()
    for part in spec:
        part = part.strip()
        if part:
            out.add(ipaddress.ip_address(part))
    return frozenset(out)


def normalize_n8n_url(value):
    """http(s)://host[:port][/prefix] without a trailing slash, or None when unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip().rstrip("/")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.query or parts.fragment or parts.username or parts.password:
        return None
    return value


@dataclass
class Config:
    data_dir: pathlib.Path = pathlib.Path("/data")
    n8n_url: str | None = None
    n8n_key: str | None = field(default=None, repr=False)
    log_level: str = "info"
    core_ws_url: str = CORE_WS
    allow: frozenset = field(default_factory=lambda: parse_ips(DEFAULT_ALLOW))
    # X-Remote-User-* are trusted only from these peers (the Supervisor). Loopback joins only in dev mode.
    trust_user_from: frozenset = field(default_factory=lambda: parse_ips([SUPERVISOR_IP]))
    dev: bool = False
    time_scale: float = 1.0
    config_error: str | None = None

    @property
    def db_path(self) -> pathlib.Path:
        return self.data_dir / "bridge.db"


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    data_dir = pathlib.Path(env.get("BRIDGE_DATA", "/data"))
    options_path = pathlib.Path(env.get("BRIDGE_OPTIONS", str(data_dir / "options.json")))
    options = {}
    try:
        options = json.loads(options_path.read_text())
        if not isinstance(options, dict):
            options = {}
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        LOG.warning("options file unreadable; using defaults")
    cfg = Config(data_dir=data_dir)
    raw_url = env.get("BRIDGE_N8N_URL", options.get("n8n_url"))
    cfg.n8n_url = normalize_n8n_url(raw_url)
    if cfg.n8n_url is None:
        cfg.config_error = "n8n_url is not set" if not raw_url else "n8n_url is not a valid http(s) URL"
    key = env.get("BRIDGE_N8N_KEY", options.get("n8n_key"))
    cfg.n8n_key = key if isinstance(key, str) and key.strip() else None
    level = str(env.get("BRIDGE_LOG_LEVEL", options.get("log_level") or "info")).lower()
    cfg.log_level = level if level in LOG_LEVELS else "info"
    cfg.core_ws_url = env.get("BRIDGE_CORE_WS", CORE_WS)
    cfg.dev = env.get("BRIDGE_DEV") == "1"
    if cfg.dev:
        cfg.trust_user_from = parse_ips([SUPERVISOR_IP, "127.0.0.1", "::1"])
    try:
        cfg.time_scale = float(env.get("BRIDGE_TIME_SCALE", "1"))
    except ValueError:
        cfg.time_scale = 1.0
    return cfg


class RedactFilter(logging.Filter):
    """Defence in depth: a log line that would contain a secret has it masked."""

    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 4]

    def filter(self, record):
        if not self.secrets:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        masked = msg
        for s in self.secrets:
            masked = masked.replace(s, "***")
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def setup_logging(cfg: Config, secrets=()) -> None:
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    flt = RedactFilter([cfg.n8n_key, os.environ.get("SUPERVISOR_TOKEN"), *secrets])
    for handler in root.handlers:
        handler.addFilter(flt)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
