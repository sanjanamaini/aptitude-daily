#!/usr/bin/env python3
"""The aptitude website in a real browser (Playwright Chromium), on a throwaway copy of the state.

  python test_web.py [--home <state home, default from config>] [--shots DIR]   (needs: pip install playwright; playwright install chromium)

Copies the state (so the real sets are untouched), starts the server on a spare port, then:
answers questions, reloads (answers kept), checks the key stays locked, submits, checks the
score and explanations, visits Sets, Progress, Mistakes and Health, and takes screenshots in
light, dark and phone width. Any console error or CSP violation fails the run.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    ap = argparse.ArgumentParser()
    sys.path.insert(0, HERE)
    import store
    ap.add_argument("--home", default=store.home())
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    tmp = tempfile.mkdtemp(prefix="aptitude-web-")
    shots = a.shots or os.path.join(tmp, "shots")
    os.makedirs(shots, exist_ok=True)
    shutil.copytree(os.path.join(a.home, "state"), os.path.join(tmp, "state"),
                    ignore=shutil.ignore_patterns("draft.json", "attempt.json", ".*.lock"))
    port = free_port()
    env = dict(os.environ, APTITUDE_HOME=tmp, APTITUDE_QUIET="1")
    srv = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py"), "--port", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = "http://127.0.0.1:%d/" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "api/status", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    fails, checks = [], 0

    def ok(cond, what):
        nonlocal checks
        checks += 1
        print(("PASS " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "#/today")
            page.wait_for_selector(".q", timeout=10000)
            n_q = page.locator(".q").count()
            day = page.evaluate("fetch('/api/status').then(r=>r.json()).then(j=>(j.days.find(d=>d.n>=40)||j.days[0]).day)")
            paper = page.evaluate("fetch('/api/paper?day=%s').then(r=>r.json()).then(j=>j.paper)" % day)
            N = len(paper["questions"])
            S = len(paper["sections"])
            kinds = {g["section"] for g in paper["groups"].values()}
            has_code = any(q["section"] == "AA" and q["topic"].startswith("programming") for q in paper["questions"])
            print("testing %s: %d questions, %d sections, sets in %s" % (day, N, S, sorted(kinds)))
            page.goto(base + "#/day/" + day)
            page.wait_for_selector(".q")
            n_q = page.locator(".q").count()
            ok(n_q == N, "%d question cards shown (got %d)" % (N, n_q))
            stems = page.evaluate("() => Array.from(document.querySelectorAll('.q')).filter(q => (q.querySelector('.stem')||{innerText:''}).innerText.trim().length > 10).length")
            ok(stems == N, "every card shows its question text (%d of %d)" % (stems, N))
            nopts = page.evaluate("() => Array.from(document.querySelectorAll('.q')).filter(q => q.querySelectorAll('.opt').length >= 4).length")
            ok(nopts == N, "every card shows 4 or 5 options (%d of %d)" % (nopts, N))
            ok(page.locator(".stim").count() == len(paper["groups"]), "every set shows its passage, case or data")
            if "RC" in kinds:
                ok(page.locator(".stim.passage .text").count() >= 1, "RC passage rendered in the reading face")
            ok(page.locator(".chart svg").count() + page.locator("table.data").count() >= 1, "DI data drawn (chart or table)")
            if has_code:
                ok(page.locator("pre.code").count() >= 1, "program code shown as code")
            ok(page.locator(".explain").count() == 0, "no explanations before submitting")
            ok(page.locator("#sheetbar").is_visible(), "answer bar visible")
            page.screenshot(path=os.path.join(shots, "1-today-top.png"))
            chart = page.locator(".chart").first
            if chart.count():
                chart.scroll_into_view_if_needed()
                chart.screenshot(path=os.path.join(shots, "2-chart.png"))
            # answer the first 10: pick option A, then change Q1 to B
            for i in range(10):
                page.locator(".q").nth(i).locator(".opt").first.click()
            page.locator("#q1 .opt").nth(1).click()
            ok(page.locator("#sb-answered").inner_text() == "10/%d" % N, "answer count updates")
            page.locator("#q3 .flag-btn").click()
            ok(page.locator("#sb-flagged").inner_text() == "1", "flag count updates")
            page.locator("#sb-timer").click()
            time.sleep(2.2)
            page.locator("#sb-timer").click()
            ok(page.locator("#sb-time").inner_text() != "00:00", "timer runs")
            time.sleep(1.2)  # let the draft save
            page.reload()
            page.wait_for_selector(".q")
            ok(page.locator("#sb-answered").inner_text() == "10/%d" % N, "answers survive a reload")
            ok(page.locator("#q1 .opt.sel .L").inner_text() == "B", "changed answer kept")
            ok(page.locator("#q3.flagged").count() == 1, "flag survives a reload")
            try:
                urllib.request.urlopen(base + "api/key?day=" + day, timeout=3)
                locked = 200
            except urllib.error.HTTPError as e:
                locked = e.code
            ok(locked == 403, "key endpoint locked before submit (%s)" % locked)
            # the phone layout while the set is still open (the answer bar is showing)
            ph = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True).new_page()
            ph.goto(base + "#/day/" + day)
            ph.wait_for_selector(".q")
            wide_open = ph.evaluate("document.documentElement.scrollWidth")
            ok(wide_open <= 392, "no sideways scroll on a phone while solving (width %d)" % wide_open)
            ph.screenshot(path=os.path.join(shots, "8a-phone-solving.png"))
            ph.context.close()
            page.locator("#sb-submit").click()
            ok(page.locator("#modal").is_visible(), "submit asks to confirm")
            page.locator("#modal-ok").click()
            page.wait_for_selector(".score-card")
            ok(page.locator(".explain").count() == N, "%d explanations after submit" % N)
            ok(page.locator(".opt.right").count() == N, "every correct option marked")
            ok(not page.locator("#sheetbar").is_visible(), "answer bar hidden after submit")
            ok(page.locator(".score-big").inner_text().strip().endswith("/ %d" % N), "score card shows x / %d" % N)
            ok("Net" in page.locator(".score-meta").inner_text(), "score card shows the XAT net score")
            ok(page.locator(".sec-row").count() == S, "score card shows all %d sections" % S)
            page.screenshot(path=os.path.join(shots, "3-scored.png"))
            page.locator(".jump .chip", has_text="Wrong").click()
            ok(page.locator(".q").count() <= 10, "Wrong filter narrows the list")
            page.goto(base + "#/sets")
            page.wait_for_selector(".set-row")
            ok(page.locator(".set-row").count() >= 1, "Sets page lists the day")
            page.goto(base + "#/progress")
            page.wait_for_selector(".tile")
            ok(page.locator(".tile").count() == 4, "Progress tiles shown")
            page.screenshot(path=os.path.join(shots, "4-progress.png"))
            page.goto(base + "#/mistakes")
            page.wait_for_selector("#mistakes-list .q")
            nm = page.locator("#mistakes-list .q").count()
            ok(nm >= N - 10, "Mistakes lists wrong and skipped questions (%d)" % nm)
            ok(page.locator("#mistakes-list .reveal").count() == nm, "each mistake has a Show the answer button")
            page.locator("#mistakes-list .q").first.locator(".opt").first.click()
            ok(page.locator("#mistakes-list .q").first.locator(".explain").count() == 1, "redo reveals the explanation")
            page.screenshot(path=os.path.join(shots, "5-mistakes.png"))
            page.goto(base + "#/health")
            page.wait_for_selector("#health-body .card")
            ok("recomputed by code" in page.locator("#health-body").inner_text(), "Health shows how answers were checked")
            page.screenshot(path=os.path.join(shots, "6-health.png"))
            # dark mode and phone width
            page.emulate_media(color_scheme="dark")
            page.goto(base + "#/day/" + day)
            page.wait_for_selector(".score-card")
            page.screenshot(path=os.path.join(shots, "7-dark.png"))
            m = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            mp = m.new_page()
            mp.on("console", lambda x: errors.append(x.text) if x.type == "error" else None)
            mp.goto(base + "#/day/" + day)
            mp.wait_for_selector(".q")
            wide = mp.evaluate("document.documentElement.scrollWidth")
            ok(wide <= 392, "no sideways scroll on a phone (width %d)" % wide)
            mp.screenshot(path=os.path.join(shots, "8-phone.png"))
            ok(not errors, "no console errors or CSP violations %s" % (errors[:3] if errors else ""))
            br.close()
    finally:
        srv.terminate()
        shutil.rmtree(os.path.join(tmp, "state"), ignore_errors=True)
    print("\n%d of %d checks passed. Screenshots: %s" % (checks - len(fails), checks, shots))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
