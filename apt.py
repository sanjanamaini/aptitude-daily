#!/usr/bin/env python3
"""Aptitude Daily: XAT and BITS BAAT practice, 50 new checked questions every morning.

A launchd job builds the day's set (Claude writes the questions; code and a blind second
solve check every answer), the website http://127.0.0.1:8643 shows them, and the answer key
unlocks only once the set is submitted (on the page, or with `submit` / `unlock` here).

  python3 apt.py status                  # sets, solved or not, scores
  python3 apt.py generate                # build today's set now (--force replaces an unsolved one)
  python3 apt.py submit --answers "1B 2C 3D ..."   # grade, unlock, print the key
  python3 apt.py unlock                  # solved on paper: unlock without a score
  python3 apt.py key [--wrong | --full]  # print an unlocked key
  python3 apt.py fix --q 12 --answer C --why "..."   # a key proved wrong: correct on the record, re-score
  python3 apt.py brief                   # one line for a Claude Code SessionStart hook
  python3 apt.py deploy [--time 06:30]   # copy to <home>/app and install the two launchd agents (macOS)
  python3 apt.py sync [--to DIR]         # copy sets, unlocked keys and scores into a folder you keep

macOS blocks scheduled jobs from ~/Documents, so the scheduled copy runs from <home>/app and
all state lives in <home>/state (home from config.json, default ~/.aptitude-daily).
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import store  # noqa: E402
import score  # noqa: E402

APP_FILES = ["apt.py", "build.py", "config.json", "llm.py", "plan.py", "prompts.py", "sandbox_runner.py",
             "score.py", "server.py", "store.py", "verify.py"]
PY312 = "/opt/homebrew/bin/python3.12"
RETRY_TIMES = ["09:30", "13:00", "18:00"]  # if the morning build failed or the Mac was off, these catch it


def labels(cfg=None):
    cfg = cfg or store.load_config()
    lc = cfg.get("launchd") or {}
    return lc.get("label", "local.aptitude-daily"), lc.get("web_label", "local.aptitude-daily.web")


def url(cfg=None):
    cfg = cfg or store.load_config()
    return "http://127.0.0.1:%d/" % cfg["web_port"]


def mac_notify(title, body):
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"' % (body.replace('"', "'"), title)],
                       timeout=20, capture_output=True)
    except Exception:
        pass


def latest_day(unsolved_only=False):
    for d in store.days():
        if not unsolved_only or not store.unlocked(d):
            return d
    return None


def pick_day(a, need_unsolved=False):
    day = getattr(a, "day", None) or (store.today() if store.paper(store.today()) else latest_day())
    if not day or not store.paper(day):
        print("no set found%s" % ((" for %s" % a.day) if getattr(a, "day", None) else ""))
        sys.exit(2)
    return day


# ------------------------------------------------------------------ generate

def cmd_generate(a):
    cfg = store.load_config()
    store.ensure_dirs()
    day = a.day or store.today()
    if not store.valid_day(day):
        print("bad day %r" % day)
        return 2
    if store.paper(day) and not a.force:
        msg = "%s already has a set; skipping" % day
        if a.scheduled:
            store.log(msg)
        else:
            print(msg + " (use --force to replace it while it is unsolved)")
        return 0
    rs = store.run_state()
    if rs.get("running"):
        store.log("a build is already running (pid %s, since %s); not starting another" % (rs.get("pid"), rs.get("started")))
        return 0
    import build
    import llm
    if not llm.available():
        store.log("claude CLI not found; cannot build")
        store.save(store.state("run.json"), {"running": False, "day": day, "ok": False, "finished": store.now(),
                                             "summary": "claude CLI not found"})
        return 1
    started = store.now()
    store.save(store.state("run.json"), {"running": True, "pid": os.getpid(), "day": day, "started": started,
                                         "trigger": "schedule" if a.scheduled else ("website" if a.web else "manual"),
                                         "sections": {}})
    store.log("build start for %s (%s)" % (day, "scheduled" if a.scheduled else "manual"))
    try:
        b = build.Builder(cfg, day)
        secs = [s.strip().upper() for s in a.sections.split(",")] if a.sections else None
        paper, key, meta = b.run(secs)
        n = len(paper["questions"])
        need = int(math.ceil(float(cfg["verify"].get("min_publish_fraction", 0.9)) * meta["planned"]))
        if n < need:
            raise RuntimeError("only %d of %d questions passed the checks (at least %d needed); not published, the next "
                               "scheduled try builds it again" % (n, meta["planned"], need))
        build.publish(paper, key, meta, force=a.force)
        summary = "%d questions ready (%d planned); %d drafts dropped by the checks; %d answers recomputed by code, %d confirmed by a blind solve; %d min" % (
            n, meta["planned"], meta["rejected"], meta["code_checked"], meta["solver_agreed"], meta["seconds"] // 60)
        store.log(summary)
        rs = store.load(store.state("run.json"), {}) or {}
        rs.update({"running": False, "pid": None, "finished": store.now(), "ok": True, "summary": summary})
        store.save(store.state("run.json"), rs)
        if a.scheduled or a.notify or a.web:
            short = "" if n >= meta["planned"] else " (%d of %d passed the checks)" % (n, meta["planned"])
            mac_notify("Aptitude", "Today's %d questions are ready%s. Open %s" % (n, short, url(cfg)))
        return 0
    except BaseException as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:300])
        store.log("build failed: " + msg)
        rs = store.load(store.state("run.json"), {}) or {}
        rs.update({"running": False, "pid": None, "finished": store.now(), "ok": False, "summary": "build failed: " + msg})
        store.save(store.state("run.json"), rs)
        if a.scheduled or a.notify or a.web:
            mac_notify("Aptitude", "Today's set could not be built (%s). It retries later; Health on %s has details." % (type(e).__name__, url(cfg)))
        if isinstance(e, KeyboardInterrupt):
            raise
        return 1


# ------------------------------------------------------------------ answer key

def key_lines(day, which="compact"):
    p, k, att = store.paper(day), store.key(day), store.attempt(day)
    res = (att or {}).get("results", {})
    mine = (att or {}).get("answers", {})
    lines = []
    if which == "compact":
        for s in p["sections"]:
            items = []
            for q in p["questions"][s["first"] - 1:s["last"]]:
                n = str(q["n"])
                mark = {"correct": "", "wrong": " (you %s)" % mine.get(n, ""), "skipped": " (skipped)"}.get(res.get(n), "")
                items.append("%s %s%s" % (n, k["answers"][n]["answer"], mark))
            lines.append("%s, questions %d-%d: %s" % (s["name"], s["first"], s["last"], ", ".join(items)))
        return lines
    for q in p["questions"]:
        n = str(q["n"])
        if which == "wrong" and res.get(n) not in ("wrong", "skipped"):
            continue
        a = k["answers"][n]
        state = res.get(n)
        head = "Q%s (%s, %s, %s): answer %s" % (n, q["section"], q["topic"], q["difficulty"], a["answer"])
        if state == "wrong":
            head += ", you chose %s" % mine.get(n, "")
        elif state == "skipped":
            head += ", you skipped it"
        lines.append(head)
        lines.append("  " + a["explanation"].replace("\n", "\n  "))
        if a.get("shortcut"):
            lines.append("  Shortcut: " + a["shortcut"])
        lines.append("")
    return lines


def summary_lines(att):
    if not score.scored(att):
        return ["Unlocked without a score (solved on paper). Send your answers as 1B 2C ... any time to get scored."]
    t = att["totals"]
    out = ["Score: %d correct, %d wrong, %d skipped of %d. Accuracy %s. XAT-style net %.2f (+1, -0.25)." % (
        t["correct"], t["wrong"], t["skipped"], t["total"],
        ("%d%%" % round(100 * t["accuracy"])) if t["accuracy"] is not None else "n/a", t["net"])]
    for code, s in att["sections"].items():
        out.append("  %s: %d of %d right%s" % (code, s["correct"], s["total"],
                                               (", %d wrong" % s["wrong"]) if s["wrong"] else ""))
    if att.get("elapsed_s"):
        out.append("  Time: %d min" % round(att["elapsed_s"] / 60.0))
    return out


def save_attempt(day, att):
    with store.lock():
        store.save(store.day_file(day, "attempt.json"), att)


def cmd_submit(a):
    cfg = store.load_config()
    day = pick_day(a)
    old = store.attempt(day)
    if old and score.scored(old) and not a.replace:
        print("%s was already submitted (%s, %s). Showing that result; add --replace to grade new answers." % (day, old.get("submitted_at"), old.get("mode")))
        print("\n".join(summary_lines(old)))
        return 0
    p, k = store.paper(day), store.key(day)
    answers, probs = score.parse_answers(a.answers, p)
    for pr in probs:
        print("note: " + pr)
    if not answers:
        print("no answers read, nothing submitted")
        return 2
    elapsed = int(a.minutes * 60) if a.minutes else None
    att = score.grade(p, k, answers, cfg["scoring"], "chat", elapsed, store.now())
    save_attempt(day, att)
    print("Set %s, unlocked." % day)
    print("\n".join(summary_lines(att)))
    print("")
    print("\n".join(key_lines(day, "compact")))
    print("\nFull explanations: %s#/day/%s" % (url(cfg), day))
    return 0


def cmd_unlock(a):
    cfg = store.load_config()
    day = pick_day(a)
    if not store.unlocked(day):
        save_attempt(day, score.self_check(store.paper(day), store.now()))
        print("Set %s, unlocked (no score: answers were not entered)." % day)
    else:
        print("Set %s was already unlocked." % day)
    print("\n".join(key_lines(day, "compact")))
    print("\nFull explanations: %s#/day/%s" % (url(cfg), day))
    return 0


def cmd_key(a):
    day = pick_day(a)
    if not store.unlocked(day):
        print("%s is still locked: it unlocks once the set is submitted (run submit with the answers, or unlock)." % day)
        return 3
    att = store.attempt(day)
    print("\n".join(summary_lines(att)))
    print("")
    which = "full" if a.full else ("wrong" if a.wrong else "compact")
    print("\n".join(key_lines(day, which)))
    return 0


def cmd_fix(a):
    """A key that turned out wrong (disputed, and a careful re-solve agrees): correct it on the
    record, keep the original, re-score the attempt. Never used to change a key silently."""
    cfg = store.load_config()
    day = pick_day(a)
    p, k = store.paper(day), store.key(day)
    n = str(a.q)
    q = next((x for x in p["questions"] if str(x["n"]) == n), None)
    if not q:
        print("no question %s on %s" % (n, day))
        return 2
    new = a.answer.strip().upper()
    if new not in score.LETTERS[:len(q["options"])]:
        print("answer must be one of %s" % score.LETTERS[:len(q["options"])])
        return 2
    entry = k["answers"][n]
    if entry["answer"] == new:
        print("Q%s already keyed %s" % (n, new))
        return 0
    entry.setdefault("original_answer", entry["answer"])
    entry["answer"] = new
    entry["correction"] = {"on": store.now(), "from": entry["original_answer"], "to": new, "why": a.why}
    entry["explanation"] = "Corrected on %s (the original key said %s): %s\n\n%s" % (store.today(), entry["original_answer"], a.why, entry["explanation"])
    with store.lock():
        store.save(store.day_file(day, "key.json"), k)
    print("Q%s on %s re-keyed %s -> %s" % (n, day, entry["original_answer"], new))
    att = store.attempt(day)
    if score.scored(att):
        att2 = score.grade(p, k, att["answers"], cfg["scoring"], att["mode"], att.get("elapsed_s"), att.get("submitted_at"))
        att2["regraded_at"] = store.now()
        save_attempt(day, att2)
        print("re-scored: " + summary_lines(att2)[0])
    return 0


# ------------------------------------------------------------------ status and brief

def cmd_status(a):
    cfg = store.load_config()
    rs = store.run_state()
    if rs.get("running"):
        print("building %s since %s: %s" % (rs.get("day"), rs.get("started"), json.dumps(rs.get("sections", {}))))
    elif rs:
        print("last build: %s (%s)" % (rs.get("summary", "?"), rs.get("finished", "?")))
    for d in store.days()[: a.n]:
        p, m, att = store.paper(d), store.meta(d), store.attempt(d)
        if not att:
            st = "not solved"
        elif not score.scored(att):
            st = "unlocked, no score"
        else:
            t = att["totals"]
            st = "solved: %d/%d right, net %.2f" % (t["correct"], t["total"], t["net"])
        print("%s  %d questions  %s  (dropped %s drafts)" % (d, len(p["questions"]), st, m.get("rejected", "?")))
    if not store.days():
        print("no sets yet")
    print("website: %s" % url(cfg))
    return 0


def cmd_brief(a):
    """For a Claude Code SessionStart hook: what Claude should know about today's set, in one or two lines."""
    try:
        today = store.today()
        rs = store.run_state()
        out = []
        if store.paper(today):
            if not store.unlocked(today):
                out.append("Aptitude: today's set (%s, %d questions) is ready and still locked. Once it is solved, unlock it (apt.py submit with the answers, or apt.py unlock); never reveal an answer before that." % (today, len(store.paper(today)["questions"])))
            else:
                att = store.attempt(today)
                out.append("Aptitude: today's set is solved%s." % ((", %d/%d right" % (att["totals"]["correct"], att["totals"]["total"])) if score.scored(att) else " (unlocked without a score)"))
        elif rs.get("running"):
            out.append("Aptitude: today's set is being built now.")
        elif rs.get("day") == today and rs.get("ok") is False:
            out.append("Aptitude: today's build failed (%s). apt.py generate --notify retries it." % rs.get("summary", "")[:160])
        older = [d for d in store.days() if d != today and not store.unlocked(d)][:5]
        if older:
            out.append("Aptitude: older sets not yet solved: %s." % ", ".join(older))
        if out:
            print(" ".join(out))
    except Exception:
        pass
    return 0


# ------------------------------------------------------------------ deploy and sync

def _plist(label, args, home, path_env, extra, short):
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{args}</array>
{extra}
  <key>WorkingDirectory</key><string>{home}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StandardOutPath</key><string>{home}/logs/{short}.out.log</string>
  <key>StandardErrorPath</key><string>{home}/logs/{short}.err.log</string>
</dict></plist>
""".format(label=label, args="".join("<string>%s</string>" % x for x in args), extra=extra, home=home,
           path=path_env, short=short)


def _load(plist, label):
    """Reload one agent. bootout returns before launchd has finished unloading, and a bootstrap
    in that window fails ("Bootstrap failed: 5", seen 2026-11-21, which left the website down):
    wait until the old one is gone, then retry a few times."""
    uid = str(os.getuid())
    target = "gui/%s/%s" % (uid, label)
    subprocess.run(["launchctl", "bootout", target], capture_output=True)
    for _ in range(50):
        if subprocess.run(["launchctl", "print", target], capture_output=True).returncode != 0:
            break
        time.sleep(0.2)
    r = None
    for attempt in range(4):
        r = subprocess.run(["launchctl", "bootstrap", "gui/%s" % uid, plist], capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(1.0 + attempt)
    return r


def cmd_deploy(a):
    cfg = store.load_config()
    home = store.home()
    app = os.path.join(home, "app")
    store.ensure_dirs()
    os.makedirs(os.path.join(app, "web"), exist_ok=True)
    LABEL, WEB_LABEL = labels(cfg)
    files = APP_FILES + (["config.local.json"] if os.path.exists(os.path.join(HERE, "config.local.json")) else [])
    for f in files:
        shutil.copyfile(os.path.join(HERE, f), os.path.join(app, f))
    web = [f for f in os.listdir(os.path.join(HERE, "web")) if not f.startswith(".")]
    for f in web:
        shutil.copyfile(os.path.join(HERE, "web", f), os.path.join(app, "web", f))
    print("copied %d files and %d website files to %s" % (len(files), len(web), app))
    py = PY312 if os.path.exists(PY312) else sys.executable
    path_env = ":".join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    t = a.time or cfg["schedule"]
    times = [t] + [x for x in RETRY_TIMES if x > t]
    cal = "".join("<dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>%d</integer></dict>" % (int(x.split(":")[0]), int(x.split(":")[1])) for x in times)
    plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LABEL)
    with open(plist, "w") as f:
        f.write(_plist(LABEL, [py, app + "/apt.py", "generate", "--scheduled"], home, path_env,
                       "  <key>StartCalendarInterval</key><array>%s</array>\n  <key>RunAtLoad</key><true/>" % cal, "launchd"))
    rs = store.run_state()
    if rs.get("running"):
        print("daily build: a build is running now, so the schedule was left as it is (run deploy again after it ends)")
    else:
        r = _load(plist, LABEL)
        print("daily build at %s (retries %s if the set is missing; also at login): %s" % (
            t, ", ".join(times[1:]) or "none", "ok" if r.returncode == 0 else r.stderr.strip()))
    wplist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % WEB_LABEL)
    with open(wplist, "w") as f:
        f.write(_plist(WEB_LABEL, [py, app + "/server.py", "--port", str(cfg["web_port"])], home, path_env,
                       "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n  <key>ThrottleInterval</key><integer>5</integer>", "web"))
    r = _load(wplist, WEB_LABEL)
    up = False
    import urllib.request
    for _ in range(40):
        try:
            urllib.request.urlopen(url(cfg) + "api/status", timeout=1)
            up = True
            break
        except Exception:
            time.sleep(0.25)
    print("website: %s at %s %s" % ("running" if up else "NOT answering", url(cfg), "" if r.returncode == 0 else "(%s)" % r.stderr.strip()))
    return 0 if up else 1


def cmd_sync(a):
    """Copy every set into <dir>/<day>/ (the key only once unlocked) and write <dir>/progress.md."""
    cfg = store.load_config()
    target = a.to or cfg.get("sync_dir")
    if not target:
        print("no folder given: use --to DIR or set sync_dir in config.local.json")
        return 2
    out = os.path.expanduser(target)
    os.makedirs(out, exist_ok=True)
    n = 0
    for d in store.days():
        dst = os.path.join(out, d)
        os.makedirs(dst, exist_ok=True)
        files = ["paper.json", "meta.json"] + (["key.json", "attempt.json"] if store.unlocked(d) else [])
        for f in files:
            src = store.day_file(d, f)
            if os.path.exists(src):
                shutil.copyfile(src, os.path.join(dst, f))
        n += 1
    rows = [(d, store.paper(d), store.attempt(d)) for d in store.days()]
    st = score.rollup(rows, store.today(), window=28)
    lines = ["# Aptitude progress (XAT 2027 and BITS BAAT)", "",
             "Built by `apt.py sync` on %s. Sets: %d, solved: %d, streak: %d day(s)." % (
                 store.today(), len(rows), st["solved_days"], st["streak"]), "",
             "## Sections, last 28 days", ""]
    names = store.load_config()["sections"]
    for code, s in sorted(st["sections"].items()):
        acc = ("%d%%" % round(100 * s["accuracy"])) if s["accuracy"] is not None else "n/a"
        lines.append("- %s: %d of %d attempted right (%s), %d skipped" % (names.get(code, {}).get("name", code), s["correct"], s["attempted"], acc, s["skipped"]))
    weak = sorted([t for t in st["topics"].values() if t["attempted"] >= 2], key=lambda t: (t["accuracy"], -t["attempted"]))[:10]
    if weak:
        lines += ["", "## Weakest topics (2+ attempts)", ""]
        for t in weak:
            lines.append("- %s, %s: %d of %d right" % (t["section"], t["topic"], t["correct"], t["attempted"]))
    lines += ["", "## Days", ""]
    for d in st["days"]:
        tt = d.get("totals")
        if tt and tt.get("attempted"):
            lines.append("- %s: %d/%d right, %d wrong, %d skipped, net %.2f" % (d["day"], tt["correct"], tt["total"], tt["wrong"], tt["skipped"], tt["net"]))
        else:
            lines.append("- %s: %s" % (d["day"], "unlocked, no score" if d["solved"] else "not solved"))
    with open(os.path.join(out, "progress.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("synced %d set(s) into %s" % (n, out))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--day")
    g.add_argument("--force", action="store_true")
    g.add_argument("--notify", action="store_true")
    g.add_argument("--sections", help="comma list, e.g. QA,DI (testing)")
    g.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    g.add_argument("--web", action="store_true", help=argparse.SUPPRESS)
    s = sub.add_parser("submit")
    s.add_argument("--day")
    s.add_argument("--answers", required=True)
    s.add_argument("--minutes", type=float)
    s.add_argument("--replace", action="store_true")
    u = sub.add_parser("unlock")
    u.add_argument("--day")
    k = sub.add_parser("key")
    k.add_argument("--day")
    k.add_argument("--full", action="store_true")
    k.add_argument("--wrong", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("-n", type=int, default=10)
    sub.add_parser("brief")
    fx = sub.add_parser("fix")
    fx.add_argument("--day")
    fx.add_argument("--q", required=True, type=int)
    fx.add_argument("--answer", required=True)
    fx.add_argument("--why", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--time")
    y = sub.add_parser("sync")
    y.add_argument("--to")
    a = ap.parse_args()
    fn = {"generate": cmd_generate, "submit": cmd_submit, "unlock": cmd_unlock, "key": cmd_key, "status": cmd_status,
          "brief": cmd_brief, "deploy": cmd_deploy, "sync": cmd_sync, "fix": cmd_fix}[a.cmd]
    return fn(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
