"""Shared state for the daily aptitude sets: config, paths, atomic JSON, day folders, the lock.

All state lives in the home folder from config (default ~/.aptitude-daily; macOS blocks
scheduled jobs from ~/Documents, so keep it outside that). One folder per day:

  state/days/<YYYY-MM-DD>/paper.json    questions, no answers (what the page shows first)
  state/days/<YYYY-MM-DD>/key.json      answers, explanations, how each answer was checked
  state/days/<YYYY-MM-DD>/meta.json     build stats (generated, rejected and why, timings)
  state/days/<YYYY-MM-DD>/draft.json    answers in progress (saved by the page)
  state/days/<YYYY-MM-DD>/attempt.json  submitted answers and score; its presence unlocks the key
  state/bank.jsonl                      every published question (dedup memory)
  state/run.json                        the current or last build
"""
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    """config.json, overridden by config.local.json (personal settings, git-ignored) when present.
    APTITUDE_NO_LOCAL=1 ignores the local file (the tests use it)."""
    with open(os.path.join(HERE, "config.json")) as f:
        cfg = json.load(f)
    local = os.path.join(HERE, "config.local.json")
    if os.path.exists(local) and not os.environ.get("APTITUDE_NO_LOCAL"):
        with open(local) as f:
            _merge(cfg, json.load(f))
    return cfg


def home():
    h = os.environ.get("APTITUDE_HOME") or load_config()["paths"]["home"]
    return os.path.expanduser(h)


def path(*parts):
    return os.path.join(home(), *parts)


def state(*parts):
    return path("state", *parts)


def ensure_dirs():
    for d in (state("days"), path("logs"), path("work")):
        os.makedirs(d, exist_ok=True)


def today():
    return date.today().isoformat()


def now():
    return datetime.now().isoformat(timespec="seconds")


def valid_day(day):
    if not isinstance(day, str) or not DAY_RE.match(day):
        return False
    try:
        date.fromisoformat(day)
        return True
    except ValueError:
        return False


def day_dir(day):
    if not valid_day(day):
        raise ValueError("bad day: %r" % (day,))
    return state("days", day)


def day_file(day, name):
    return os.path.join(day_dir(day), name)


def load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(p, obj):
    """Write JSON atomically (temp file + rename) so a reader never sees half a file."""
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@contextmanager
def lock(name="state"):
    """A file lock shared by the website, the CLI and the scheduled build."""
    os.makedirs(state(), exist_ok=True)
    with open(state(".%s.lock" % name), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def days():
    """All days that have a published paper, newest first."""
    d = state("days")
    if not os.path.isdir(d):
        return []
    out = [x for x in os.listdir(d) if valid_day(x) and os.path.exists(os.path.join(d, x, "paper.json"))]
    return sorted(out, reverse=True)


def paper(day):
    return load(day_file(day, "paper.json"))


def key(day):
    return load(day_file(day, "key.json"))


def meta(day):
    return load(day_file(day, "meta.json"), {})


def attempt(day):
    return load(day_file(day, "attempt.json"))


def draft(day):
    return load(day_file(day, "draft.json"), {"answers": {}, "flags": [], "elapsed_s": 0})


def unlocked(day):
    return os.path.exists(day_file(day, "attempt.json"))


def bank():
    out = []
    p = state("bank.jsonl")
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def append_bank(rows):
    with open(state("bank.jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def log(msg):
    ensure_dirs()
    line = "%s %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    with open(path("logs", "%s.log" % today()), "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, OSError):
        return False


def run_state():
    rs = load(state("run.json"), {}) or {}
    if rs.get("running") and not pid_alive(rs.get("pid")):
        rs["running"] = False
        rs["stale"] = True
    return rs
