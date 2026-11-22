#!/usr/bin/env python3
"""Aptitude website: http://127.0.0.1:8643, on this Mac only.

Pages (one HTML file, web/): Today (the 50 questions, a timer, answers saved as you go),
Sets (every day), Progress (sections, weak topics, streak), Mistakes (everything you got
wrong, to redo) and Health (the morning build, its checks, the log).

The answer key is never sent to the page until that day is unlocked: by Submit on the page,
or by `apt.py submit` / `apt.py unlock` (for example from a Claude Code session).

Safety: listens on 127.0.0.1 only; every request must name this host
(blocks DNS rebinding); every change needs the X-Aptitude header and a JSON body (a page on
another site cannot send that without a preflight this server refuses); a foreign Origin is
refused; strict Content-Security-Policy; question text is shown as text, never as HTML.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score  # noqa: E402
import store  # noqa: E402

MAX_BODY = 64 * 1024
WEB = os.path.join(HERE, "web")
STATIC = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "app.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}
RETRY_TIMES = ["09:30", "13:00", "18:00"]


def next_build(cfg, now=None):
    now = now or datetime.now()
    times = [cfg["schedule"]] + [t for t in RETRY_TIMES if t > cfg["schedule"]]
    today_has = bool(store.paper(now.date().isoformat()))
    for t in times:
        h, m = map(int, t.split(":"))
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if slot > now and not today_has:
            return slot.isoformat(timespec="minutes")
    h, m = map(int, cfg["schedule"].split(":"))
    return (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0).isoformat(timespec="minutes")


def exams_view(cfg):
    t = date.today()
    out = []
    for e in cfg.get("exams", []):
        x = dict(e)
        if e.get("date"):
            x["days_left"] = (date.fromisoformat(e["date"]) - t).days
        if e.get("deadline"):
            x["deadline_days_left"] = (date.fromisoformat(e["deadline"]) - t).days
        out.append(x)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Aptitude"
    sys_version = ""

    def log_message(self, fmt, *args):
        if os.environ.get("APTITUDE_QUIET"):
            return
        sys.stderr.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def host_ok(self):
        return (self.headers.get("Host") or "").lower() in self.server.allowed_hosts

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def err(self, code, msg):
        self.send(code, {"error": msg})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        obj = json.loads((self.rfile.read(n) if n else b"{}").decode("utf-8") or "{}")
        if not isinstance(obj, dict):
            raise ValueError("expected a JSON object")
        return obj

    # ---------------------------------------------------------------- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.host_ok():
            return self.err(403, "wrong host")
        u = urllib.parse.urlparse(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)
        day = (q.get("day") or [""])[0]
        try:
            if p in ("/", "/index.html"):
                return self.static("index.html")
            if re.match(r"^/(app\.js|app\.css|favicon\.svg)$", p):
                return self.static(p[1:])
            if p == "/api/status":
                return self.send(200, self.status())
            if p == "/api/paper":
                return self.api_paper(day)
            if p == "/api/key":
                return self.api_key(day)
            if p == "/api/progress":
                return self.send(200, self.progress())
            if p == "/api/mistakes":
                return self.send(200, self.mistakes())
            if p == "/api/health":
                return self.send(200, self.health())
            return self.err(404, "not found")
        except Exception as e:  # never leak a traceback to the page
            sys.stderr.write("GET %s failed: %r\n" % (p, e))
            return self.err(500, "server error: %s" % type(e).__name__)

    def do_POST(self):
        if not self.host_ok():
            return self.err(403, "wrong host")
        origin = self.headers.get("Origin")
        if origin and origin.lower().rstrip("/") not in self.server.allowed_origins:
            return self.err(403, "wrong origin")
        if self.headers.get("X-Aptitude") != "1" or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.err(403, "missing X-Aptitude header")
        p = urllib.parse.urlparse(self.path).path
        try:
            b = self.body()
            if p == "/api/draft":
                return self.api_draft(b)
            if p == "/api/submit":
                return self.api_submit(b)
            if p == "/api/generate":
                return self.api_generate(b)
            return self.err(404, "not found")
        except ValueError as e:
            return self.err(400, str(e))
        except Exception as e:
            sys.stderr.write("POST %s failed: %r\n" % (p, e))
            return self.err(500, "server error: %s" % type(e).__name__)

    def static(self, name):
        with open(os.path.join(WEB, name), "rb") as f:
            self.send(200, f.read(), STATIC[name])

    # ---------------------------------------------------------------- views
    def day_row(self, d):
        p, m, att = store.paper(d), store.meta(d), store.attempt(d)
        st = "locked" if not att else ("solved" if score.scored(att) else "unlocked")
        dr = store.draft(d)
        return {"day": d, "n": len(p["questions"]), "phase": p.get("phase"), "phase_label": p.get("phase_label"),
                "status": st, "totals": (att or {}).get("totals"), "answered": len((dr or {}).get("answers", {})),
                "submitted_at": (att or {}).get("submitted_at"), "mode": (att or {}).get("mode"),
                "planned": m.get("planned"), "rejected": m.get("rejected")}

    def status(self):
        cfg = store.load_config()
        return {"today": store.today(), "days": [self.day_row(d) for d in store.days()], "run": store.run_state(),
                "next_build": next_build(cfg), "schedule": cfg["schedule"], "exams": exams_view(cfg),
                "scoring": cfg["scoring"]}

    def need_day(self, day):
        if not store.valid_day(day) or not store.paper(day):
            raise LookupError(day)

    def api_paper(self, day):
        try:
            self.need_day(day)
        except LookupError:
            return self.err(404, "no set for that day")
        att = store.attempt(day)
        return self.send(200, {"paper": store.paper(day), "draft": store.draft(day), "unlocked": bool(att),
                               "attempt": att})

    def api_key(self, day):
        try:
            self.need_day(day)
        except LookupError:
            return self.err(404, "no set for that day")
        if not store.unlocked(day):
            return self.err(403, "locked: submit your answers first")
        k = store.key(day)
        for a in k["answers"].values():
            a.pop("check_code", None)  # kept on disk for audit; not needed on the page
        return self.send(200, {"key": k, "attempt": store.attempt(day), "meta": {kk: store.meta(day).get(kk) for kk in ("code_checked", "solver_agreed", "published", "rejected")}})

    def clean_answers(self, paper, answers):
        if not isinstance(answers, dict):
            raise ValueError("answers must be an object")
        opts = {str(q["n"]): len(q["options"]) for q in paper["questions"]}
        out = {}
        for k, v in answers.items():
            k = str(k)
            if k not in opts or v in (None, ""):
                continue
            if not isinstance(v, str) or v not in score.LETTERS[:opts[k]]:
                raise ValueError("bad answer for question %s" % k)
            out[k] = v
        return out

    def api_draft(self, b):
        day = b.get("day")
        try:
            self.need_day(day)
        except LookupError:
            return self.err(404, "no set for that day")
        if store.unlocked(day):
            return self.err(409, "already submitted")
        paper = store.paper(day)
        flags = [int(x) for x in (b.get("flags") or []) if str(x).isdigit()][:100]
        el = b.get("elapsed_s")
        el = int(el) if isinstance(el, (int, float)) and 0 <= el < 86400 else 0
        with store.lock():
            store.save(store.day_file(day, "draft.json"), {"answers": self.clean_answers(paper, b.get("answers") or {}),
                                                         "flags": flags, "elapsed_s": el, "saved_at": store.now()})
        return self.send(200, {"ok": True})

    def api_submit(self, b):
        day = b.get("day")
        try:
            self.need_day(day)
        except LookupError:
            return self.err(404, "no set for that day")
        cfg = store.load_config()
        paper, key = store.paper(day), store.key(day)
        old = store.attempt(day)
        if old and score.scored(old):
            return self.err(409, "already submitted")
        answers = self.clean_answers(paper, b.get("answers") or {})
        el = b.get("elapsed_s")
        el = int(el) if isinstance(el, (int, float)) and 0 < el < 86400 else None
        if b.get("mode") == "self-check" or not answers:
            att = score.self_check(paper, store.now()) if not old else old
        else:
            att = score.grade(paper, key, answers, cfg["scoring"], "site", el, store.now())
        with store.lock():
            store.save(store.day_file(day, "attempt.json"), att)
        return self.api_key(day)

    def api_generate(self, b):
        rs = store.run_state()
        if rs.get("running"):
            return self.send(200, {"ok": True, "message": "a build is already running"})
        today = store.today()
        if store.paper(today):
            return self.err(409, "today's set already exists")
        log = open(store.path("logs", "web-build.log"), "a")
        subprocess.Popen([sys.executable, os.path.join(HERE, "apt.py"), "generate", "--web"], cwd=store.home(),
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        return self.send(200, {"ok": True, "message": "building today's set; it takes 10 to 20 minutes"})

    def progress(self):
        rows = [(d, store.paper(d), store.attempt(d)) for d in store.days()]
        st = score.rollup(rows, store.today(), window=28)
        names = {k: v["name"] for k, v in store.load_config()["sections"].items()}
        st["names"] = names
        st["topics"] = sorted(st["topics"].values(), key=lambda t: (t["accuracy"] if t["accuracy"] is not None else 2, -t["attempted"]))
        return st

    def mistakes(self):
        out = []
        for d in store.days():
            att = store.attempt(d)
            if not score.scored(att):
                continue
            p, k = store.paper(d), store.key(d)
            for q in p["questions"]:
                n = str(q["n"])
                r = att["results"].get(n)
                if r not in ("wrong", "skipped"):
                    continue
                a = k["answers"][n]
                g = p["groups"].get(q["group"]) if q.get("group") else None
                out.append({"day": d, "n": q["n"], "section": q["section"], "topic": q["topic"], "difficulty": q["difficulty"],
                            "stem": q["stem"], "options": q["options"], "group": g, "result": r,
                            "mine": att["answers"].get(n, ""), "answer": a["answer"], "explanation": a["explanation"],
                            "shortcut": a.get("shortcut", "")})
                if len(out) >= 300:
                    return out
        return out

    def health(self):
        cfg = store.load_config()
        metas = []
        for d in store.days()[:7]:
            m = store.meta(d)
            metas.append({"day": d, "built_at": m.get("built_at"), "seconds": m.get("seconds"), "planned": m.get("planned"),
                          "published": m.get("published"), "code_checked": m.get("code_checked"),
                          "solver_agreed": m.get("solver_agreed"), "rejected": m.get("rejected"),
                          "rejected_by_stage": m.get("rejected_by_stage"), "claude_calls": m.get("claude_calls"),
                          "rejections": (m.get("rejections") or [])[:60], "notes": (m.get("notes") or [])[:20]})
        logp = store.path("logs", "%s.log" % store.today())
        tail = []
        if os.path.exists(logp):
            with open(logp, encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-150:]
        return {"run": store.run_state(), "next_build": next_build(cfg), "schedule": cfg["schedule"],
                "retries": [t for t in RETRY_TIMES if t > cfg["schedule"]], "models": cfg["models"],
                "builds": metas, "log": tail}


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    port = a.port or store.load_config()["web_port"]
    store.ensure_dirs()
    srv = Server(("127.0.0.1", port), Handler)
    srv.allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    srv.allowed_origins = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}
    sys.stderr.write("aptitude website on http://127.0.0.1:%d/\n" % port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
