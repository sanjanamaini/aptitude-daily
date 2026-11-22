#!/usr/bin/env python3
"""Tests for the aptitude engine: sandbox, number parsing, answer checks, answer parsing,
the planner, a full build with a fake Claude, and the website API (on a throwaway home).

  python3 test_apt.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="aptitude-test-")
os.environ["APTITUDE_HOME"] = TMP
os.environ["APTITUDE_QUIET"] = "1"
os.environ["APTITUDE_NO_LOCAL"] = "1"  # test the public config, never a personal config.local.json

import build  # noqa: E402
import plan as planmod  # noqa: E402
import score  # noqa: E402
import store  # noqa: E402
import verify  # noqa: E402

LET = "ABCDE"


def fake_call(system, prompt, model, timeout, cwd):
    """A stand-in for Claude. Setter: one question per slot, the answer hidden in the stem as
    'k=X'. Solver: reads 'k=X' back, except for stems containing 'TRAP' (answers wrong)."""
    if system.startswith("You are a top scorer"):
        ans = []
        for m in re.finditer(r"QUESTION id=(\S+).*?\n(.*?)\n", prompt):
            qid, stem = m.group(1), m.group(2)
            k = re.search(r"k=([A-E])", stem).group(1)
            if "TRAP" in stem:
                k = "A" if k != "A" else "B"
            ans.append({"id": qid, "work": "read it", "answer": k, "confidence": 5, "flaw": ""})
        return json.dumps({"answers": ans})
    nopt = int(re.search(r"exactly (\d) options", prompt).group(1))
    code = re.search(r"SECTION: (\w+)", prompt).group(1)
    groups, qs = [], []
    for m in re.finditer(r"- key (\w+): an? (.+?) set with (\d+) questions.*?slots ([\d, ]+)", prompt):
        key = m.group(1)
        text = ("Passage for %s about topic %s. " % (key, m.group(2))) + " ".join("word%d" % i for i in range(300))
        groups.append({"key": key, "kind": m.group(2), "title": "Set %s" % key, "text": text, "table": None, "chart": None})
        for s in m.group(4).split(","):
            qs.append((int(s), key))
    for m in re.finditer(r"- slot (\d+): topic", prompt):
        qs.append((int(m.group(1)), None))
    out = []
    for s, g in qs:
        x = 7 * s + 3
        k = LET[s % nopt]
        opts = [str(x + 10 * (i - LET.index(k))) for i in range(nopt)]
        trap = " TRAP" if s == 2 else ""
        w = "".join(chr(97 + int(c)) for c in str(s))  # a word made from the slot number (numbers are ignored by the repeat check)
        stem = "Question %s%s about %sx %sy %sz in %s with value %d%s k=%s" % (w, code.lower(), w, w, w, code, x, trap, k)
        out.append({"slot": s, "group": g, "topic": "t", "subtopic": "st", "difficulty": "medium", "stem": stem,
                    "options": opts, "answer": k, "answer_value": x,
                    "explanation": "Because the value is computed as seven times the slot plus three, which gives %d." % x,
                    "shortcut": "", "check": "result = 7 * %d + 3" % s})
    return json.dumps({"groups": groups, "questions": out})


class TestVerify(unittest.TestCase):
    def test_sandbox_computes(self):
        r = verify.run_check("import math\nresult = math.comb(5, 2) * 3")
        self.assertEqual(r, {"ok": True, "result": 30, "printed": ""})

    def test_sandbox_print_output(self):
        r = verify.run_check("for i in range(3):\n    print(i * i)")
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], "0\n1\n4")

    def test_sandbox_blocks_files_and_imports(self):
        for bad in ["open('/etc/passwd').read()", "import os\nresult = 1", "result = ().__class__",
                    "__import__('os')", "import subprocess", "eval('1+1')", "result = getattr(1, 'real')"]:
            r = verify.run_check(bad)
            self.assertFalse(r["ok"], bad)

    def test_sandbox_timeout(self):
        r = verify.run_check("while True:\n    pass", timeout=3)
        self.assertFalse(r["ok"])

    def test_parse_number(self):
        cases = {"₹1,250": 1250, "25%": 25, "3/4": 0.75, "2 1/2 hours": 2.5, "-0.5 km": -0.5, "Rs. 450": 450,
                 "−3": -3, "12 km/h": 12}
        for t, v in cases.items():
            self.assertAlmostEqual(verify.parse_number(t), v, msg=t)
        for t in ["√3/2", "3 and 4", "None of these", "2π", "log 5"]:
            self.assertIsNone(verify.parse_number(t), t)

    def test_compare(self):
        q = {"options": ["10", "12", "15", "18", "20"], "answer": "C", "answer_value": 15}
        self.assertTrue(verify.compare(15, q)[0])
        self.assertTrue(verify.compare(15.0000001, q)[0])
        self.assertFalse(verify.compare(12, q)[0])
        q2 = {"options": ["15", "12", "15.0", "18", "20"], "answer": "C", "answer_value": 15}
        self.assertFalse(verify.compare(15, q2)[0])  # two options say 15
        q3 = {"options": ["Ravi", "Meena", "Tara", "Arun", "Joy"], "answer": "B", "answer_value": "Meena"}
        self.assertTrue(verify.compare("Meena", q3)[0])
        self.assertFalse(verify.compare("Tara", q3)[0])
        self.assertFalse(verify.compare("B", q3)[0])  # a letter is not a check
        q4 = {"options": ["√2", "√3", "2", "3"], "answer": "B", "answer_value": 1.7320508}
        self.assertTrue(verify.compare(3 ** 0.5, q4)[0])
        q5 = {"options": ["0 1 4", "0 1 2", "1 4 9", "0 2 4"], "answer": "A", "answer_value": "0 1 4"}
        self.assertTrue(verify.compare("0\n1\n4", q5)[0])

    def test_compare_uses_shown_precision(self):
        years = {"options": ["2021", "2022", "2023", "2024", "2025"], "answer": "C", "answer_value": 2023}
        self.assertTrue(verify.compare(2023, years)[0])
        self.assertFalse(verify.compare(2022, years)[0])
        addr = {"options": ["1040", "1044", "1048", "1052"], "answer": "B", "answer_value": 1044}
        self.assertTrue(verify.compare(1044, addr)[0])
        rounded = {"options": ["12.5%", "12.9%", "13.3%", "11.8%", "14.0%"], "answer": "A", "answer_value": 12.48}
        self.assertTrue(verify.compare(12.4793, rounded)[0])
        self.assertFalse(verify.compare(12.6, rounded)[0])
        mixed = {"options": ["6", "6.25", "5", "7.5", "6.5"], "answer": "B", "answer_value": 6.25}
        self.assertTrue(verify.compare(6.25, mixed)[0])  # "6" is not within reach of 6.25

    def test_symbols_keep_options_distinct(self):
        sec = {"options": 4, "code_check": "optional"}
        q = {"stem": "For which k and m does the system have infinitely many solutions?",
             "options": ["k = 4, m = 12", "k = 4, m ≠ 12", "k ≠ 4", "k = 4, m < 12"], "answer": "A",
             "explanation": "Row reduce: the third row becomes (k - 4)z = m - 12, so k = 4 and m = 12.", "difficulty": "medium",
             "check": "", "group": None}
        self.assertEqual(verify.structure(q, sec, set()), [])

    def test_dedup_ignores_numbers(self):
        a = verify.shingles("A train 150 m long passes a pole in 15 seconds. Find its speed in km/h.")
        b = verify.shingles("A train 240 m long passes a pole in 12 seconds. Find its speed in km/h.")
        c = verify.shingles("Two pipes fill a tank in 6 and 9 hours. How long do both take together?")
        self.assertGreater(verify.jaccard(a, b), 0.8)
        self.assertLess(verify.jaccard(a, c), 0.1)

    def test_clean_dashes(self):
        self.assertEqual(verify.clean("cost — the price"), "cost, the price")
        self.assertEqual(verify.clean("2020–2024"), "2020-2024")

    def test_structure(self):
        sec = {"options": 5, "code_check": "required"}
        q = {"stem": "What is the value of x?", "options": ["1", "2", "3", "4", "5"], "answer": "B",
             "explanation": "x is two because the equation 3x = 6 gives x = 2 directly.", "difficulty": "easy",
             "check": "result = 6/3", "group": None}
        self.assertEqual(verify.structure(q, sec, set()), [])
        self.assertTrue(verify.structure(dict(q, options=["1", "2", "3", "4"]), sec, set()))
        self.assertTrue(verify.structure(dict(q, check=""), sec, set()))
        self.assertTrue(verify.structure(dict(q, stem="Find \\frac{1}{2} of 10"), sec, set()))
        self.assertTrue(verify.structure(dict(q, group="X1"), sec, set()))


class TestScore(unittest.TestCase):
    paper = {"day": "2026-11-21", "questions": [{"n": i, "section": "QA" if i <= 3 else "AA", "topic": "t%d" % (i % 2),
                                                  "options": ["a", "b", "c", "d", "e"] if i <= 3 else ["a", "b", "c", "d"]}
                                                 for i in range(1, 6)]}
    key = {"answers": {str(i): {"answer": "B"} for i in range(1, 6)}}

    def test_parse_pairs(self):
        a, p = score.parse_answers("1B 2-c, 3. A 4) D", self.paper)
        self.assertEqual(a, {"1": "B", "2": "C", "3": "A", "4": "D"})
        self.assertEqual(p, [])

    def test_parse_sequence(self):
        a, p = score.parse_answers("B C - A D", self.paper)
        self.assertEqual(a, {"1": "B", "2": "C", "4": "A", "5": "D"})
        a, p = score.parse_answers("BCA", self.paper)
        self.assertEqual(a, {})
        self.assertTrue(p)

    def test_parse_rejects_e_on_four_options(self):
        a, p = score.parse_answers("4E 5B", self.paper)
        self.assertEqual(a, {"5": "B"})
        self.assertTrue(p)

    def test_grade(self):
        att = score.grade(self.paper, self.key, {"1": "B", "2": "C", "4": "B"}, {"correct": 1, "wrong": -0.25, "skipped": 0}, "chat")
        self.assertEqual(att["totals"]["correct"], 2)
        self.assertEqual(att["totals"]["wrong"], 1)
        self.assertEqual(att["totals"]["skipped"], 2)
        self.assertEqual(att["totals"]["net"], 1.75)
        self.assertEqual(att["sections"]["QA"]["correct"], 1)

    def test_streak(self):
        self.assertEqual(score.streak(["2026-11-21", "2026-11-20", "2026-11-18"], "2026-11-21"), 2)
        self.assertEqual(score.streak(["2026-11-20", "2026-11-19"], "2026-11-21"), 2)
        self.assertEqual(score.streak([], "2026-11-21"), 0)


class TestPlan(unittest.TestCase):
    def test_mix_and_phase(self):
        cfg = store.load_config()
        p = planmod.make_plan(cfg, "2026-11-22", {"topics": {}, "sections": {}}, [], extra=False)
        self.assertEqual(p["phase"], "xat")
        self.assertEqual(sum(s["n"] for s in p["sections"]), 50)
        for s in p["sections"]:
            self.assertEqual(sum(g["size"] for g in s["groups"]) + len(s["slots"]), s["n"], s["code"])
        p2 = planmod.make_plan(cfg, "2027-01-05", {"topics": {}, "sections": {}}, [], extra=False)
        self.assertEqual(p2["phase"], "baat")
        self.assertNotIn("DM", [s["code"] for s in p2["sections"]])
        self.assertEqual(sum(s["n"] for s in p2["sections"]), 50)

    def test_no_same_idea_twice_a_day(self):
        cfg = store.load_config()
        for i in range(1, 29):
            day = "2026-12-%02d" % i
            p = planmod.make_plan(cfg, day, {"topics": {}, "sections": {}}, [])
            chosen = set()
            for s in p["sections"]:
                chosen |= {(s["code"], g["kind"]) for g in s["groups"]} | {(s["code"], x["topic"]) for x in s["slots"]}
            for grp in cfg["exclusive_topics"]["groups"]:
                hits = {tuple(x) for x in grp} & chosen
                self.assertLessEqual(len(hits), 1, (day, hits))

    def test_ramp_gets_harder_every_day_then_returns_to_the_mix(self):
        from datetime import date, timedelta
        cfg = store.load_config()
        self.assertIsNone(cfg["ramp"]["start"])  # off unless switched on
        cfg["ramp"]["start"] = "2026-11-22"
        W = {"easy": 1, "medium": 2, "hard": 3}

        def level(day):
            p = planmod.make_plan(cfg, day, {"topics": {}, "sections": {}}, [], extra=False)
            d = [x for s in p["sections"] for x in [y for g in s["groups"] for y in g["difficulties"]] + [z["difficulty"] for z in s["slots"]]]
            return p, sum(W[x] for x in d) / float(len(d)), d

        start = date.fromisoformat(cfg["ramp"]["start"])
        days = [(start + timedelta(days=i)).isoformat() for i in range(cfg["ramp"]["days"])]
        levels = [level(d)[1] for d in days]
        for a, b in zip(levels, levels[1:]):
            self.assertGreater(b, a)  # strictly harder every day
        p1, l1, d1 = level(days[0])
        self.assertGreaterEqual(d1.count("easy"), 40)  # day 1 is simple
        self.assertEqual(d1.count("hard"), 0)
        self.assertEqual(p1["level"]["ramp_day"], 1)
        self.assertIn("day 1 of 15", p1["level_label"])
        after = (start + timedelta(days=cfg["ramp"]["days"])).isoformat()
        pa, la, _ = level(after)
        self.assertIsNone(pa["level"])  # back to the mix of the first set, adaptive again
        _, l_first, _ = level("2026-11-21")
        self.assertAlmostEqual(la, l_first, places=6)
        self.assertGreater(levels[-1], l_first)  # the last ramp day is at least as hard as the mix

    def test_ramp_ignores_scores_but_mix_adapts_after(self):
        cfg = store.load_config()
        cfg["ramp"]["start"] = "2026-11-22"
        strong = {"topics": {}, "sections": {"QA": {"recent": [0.9, 0.95, 0.9]}}}
        p = planmod.make_plan(cfg, "2026-11-22", strong, [], extra=False)
        qa = [s for s in p["sections"] if s["code"] == "QA"][0]
        self.assertGreaterEqual([x["difficulty"] for x in qa["slots"]].count("easy"), 8)
        p2 = planmod.make_plan(cfg, "2026-12-08", strong, [], extra=False)
        qa2 = [s for s in p2["sections"] if s["code"] == "QA"][0]
        self.assertEqual(qa2["difficulty_mix"], planmod.HARD_MIX)

    def test_deterministic(self):
        cfg = store.load_config()
        a = planmod.make_plan(cfg, "2026-11-22", {"topics": {}, "sections": {}}, [])
        b = planmod.make_plan(cfg, "2026-11-22", {"topics": {}, "sections": {}}, [])
        self.assertEqual(a, b)

    def test_weak_topics_weighted_up(self):
        stats = {"topics": {"QA|probability": {"attempted": 10, "correct": 1}, "QA|percentages": {"attempted": 10, "correct": 10}}}
        w = planmod.weights(["probability", "percentages"], "QA", stats, {"probability": "2026-11-21", "percentages": "2026-11-21"}, "2026-11-22")
        self.assertGreater(w[0], 2 * w[1])

    def test_spread(self):
        import random
        d = planmod.spread(10, planmod.BASE_MIX, random.Random(1))
        self.assertEqual(len(d), 10)
        self.assertEqual(d.count("medium"), 5)


class TestBuild(unittest.TestCase):
    def test_full_build_with_fake_claude(self):
        cfg = store.load_config()
        store.ensure_dirs()
        b = build.Builder(cfg, "2026-11-21", call=fake_call, log=lambda m: None)
        paper, key, meta = b.run()
        self.assertEqual(len(paper["questions"]), 50)
        self.assertEqual(meta["published"], 50)
        self.assertGreaterEqual(meta["rejected"], 1)  # the TRAP questions
        self.assertTrue(all("TRAP" not in q["stem"] for q in paper["questions"]))
        self.assertTrue(all("answer" not in q for q in paper["questions"]))  # no key in the paper
        self.assertEqual([q["n"] for q in paper["questions"]], list(range(1, 51)))
        counts = {s["code"]: s["count"] for s in paper["sections"]}
        self.assertEqual(counts, {"QA": 30, "DI": 6, "LR": 4, "DM": 5, "VA": 5})  # mostly Quant
        self.assertEqual(paper["sections"][0]["code"], "QA")  # Quant first on the page
        di = [x for x in paper["groups"].values() if x["section"] == "DI"]
        self.assertEqual(sorted(len(x["qnums"]) for x in di), [3, 3])
        dm = [x for x in paper["groups"].values() if x["section"] == "DM"]
        self.assertEqual([len(x["qnums"]) for x in dm], [3])
        self.assertGreaterEqual(meta["claude_calls"], 3 + 4 * 2)  # Quant went out in several setter calls
        self.assertTrue(all(k["checks"]["code"] == "pass" and k["checks"]["solver"] == "agree" for k in key["answers"].values()))
        build.publish(paper, key, meta)
        self.assertEqual(store.days(), ["2026-11-21"])
        with self.assertRaises(RuntimeError):
            build.publish(paper, key, meta)  # no silent overwrite
        # the next day must not repeat today's questions: same fake stems would all be repeats
        b2 = build.Builder(cfg, "2026-11-22", call=fake_call, log=lambda m: None)
        self.assertTrue(b2.past_stems.get("QA"))


class TestLimits(unittest.TestCase):
    def test_limit_reset_parsing(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Chicago")
        now = datetime(2026, 11, 22, 10, 53, tzinfo=tz)
        msg = "claude error: You've hit your 5-hour limit \u00b7 resets 11:30am (America/Chicago)"
        r = build.limit_reset(msg, now)
        self.assertEqual(datetime.fromtimestamp(r - 90, tz).strftime("%Y-%m-%d %H:%M"), "2026-11-22 11:30")
        later = datetime(2026, 11, 22, 23, 0, tzinfo=tz)
        r2 = build.limit_reset(msg, later)  # already past today: tomorrow
        self.assertEqual(datetime.fromtimestamp(r2 - 90, tz).strftime("%Y-%m-%d %H:%M"), "2026-11-23 11:30")
        r3 = build.limit_reset("You've hit your weekly limit \u00b7 resets Mon 9am (America/Chicago)", now)
        self.assertEqual(datetime.fromtimestamp(r3 - 90, tz).strftime("%a %H:%M"), "Mon 09:00")
        self.assertIsNone(build.limit_reset("claude gave no JSON (exit 1): boom", now))
        self.assertIsNotNone(build.limit_reset("usage limit reached", now))  # unreadable time: still a limit

    def test_build_waits_for_the_limit_then_finishes(self):
        import time as _t
        cfg = store.load_config()
        home = os.path.join(TMP, "limit")
        os.environ["APTITUDE_HOME"] = home
        real = build.limit_reset
        state = {"hit": 0}

        def limited(system, prompt, model, timeout, cwd):
            if state["hit"] < 3:
                state["hit"] += 1
                raise RuntimeError("claude error: You've hit your 5-hour limit \u00b7 resets 11:30am (America/Chicago)")
            return fake_call(system, prompt, model, timeout, cwd)

        build.limit_reset = lambda msg, now=None: (_t.time() + 1.0) if "limit" in msg else None
        try:
            store.ensure_dirs()
            logs = []
            b = build.Builder(cfg, "2026-11-23", call=limited, log=logs.append)
            t0 = _t.time()
            paper, key, meta = b.run()
            self.assertEqual(len(paper["questions"]), 50)  # nothing lost to the limit
            self.assertGreaterEqual(_t.time() - t0, 0.9)  # it really waited
            self.assertTrue(any("usage limit reached; waiting until" in m for m in logs))
        finally:
            build.limit_reset = real
            os.environ["APTITUDE_HOME"] = TMP

    def test_chunks_keep_topics_together(self):
        sp = {"code": "QA", "n": 30, "groups": [{"key": "QA1"}],
              "slots": [{"topic": "t%d" % (i % 10), "difficulty": "easy"} for i in range(36)]}
        cs = build.chunks(sp, 12)
        self.assertEqual(len(cs), 3)
        self.assertEqual(sum(len(c["slots"]) for c in cs), 36)
        self.assertEqual(cs[0]["groups"], [{"key": "QA1"}])
        self.assertEqual(cs[1]["groups"], [])
        seen = {}
        for c in cs:
            for s in c["slots"]:
                seen.setdefault(s["topic"], set()).add(c["_chunk"])
        self.assertGreaterEqual(sum(1 for v in seen.values() if len(v) == 1), 8)  # most topics stay in one call
        small = build.chunks(dict(sp, slots=sp["slots"][:12]), 12)
        self.assertEqual(len(small), 1)
        self.assertEqual(small[0]["_label"], "QA")


class TestCli(unittest.TestCase):
    def test_submit_unlock_fix_via_cli(self):
        import subprocess
        home = os.path.join(TMP, "cli")
        os.environ["APTITUDE_HOME"] = home  # its own home, so the server tests see only their day
        try:
            self._run_cli(subprocess, home)
        finally:
            os.environ["APTITUDE_HOME"] = TMP

    def _run_cli(self, subprocess, home):
        cfg = store.load_config()
        store.ensure_dirs()
        day = "2026-11-25"
        if not store.paper(day):
            b = build.Builder(cfg, day, call=fake_call, log=lambda m: None)
            b.bank = []
            b.past_stems, b.past_groups = {}, []
            paper, key, meta = b.run()
            build.publish(paper, key, meta)
        env = dict(os.environ, APTITUDE_HOME=home)
        run = lambda *x: subprocess.run([sys.executable, os.path.join(HERE, "apt.py")] + list(x), capture_output=True, text=True, env=env)
        r = run("key", "--day", day)
        self.assertEqual(r.returncode, 3)  # locked
        k = store.key(day)["answers"]
        wrong = "A" if k["2"]["answer"] != "A" else "B"
        r = run("submit", "--day", day, "--answers", "1%s 2%s" % (k["1"]["answer"], wrong))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 correct, 1 wrong, 48 skipped", r.stdout)
        r = run("fix", "--day", day, "--q", "2", "--answer", wrong, "--why", "test: the re-solve shows the original key was wrong")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 correct, 0 wrong", r.stdout)
        self.assertEqual(store.key(day)["answers"]["2"]["original_answer"], k["2"]["answer"])
        r = run("brief")
        self.assertEqual(r.returncode, 0)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        if not store.paper("2026-11-21"):
            TestBuild().test_full_build_with_fake_claude()
        cls.srv = server.Server(("127.0.0.1", 0), server.Handler)
        port = cls.srv.server_address[1]
        cls.srv.allowed_hosts = {"127.0.0.1:%d" % port}
        cls.srv.allowed_origins = {"http://127.0.0.1:%d" % port}
        cls.base = "http://127.0.0.1:%d" % port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json", "X-Aptitude": "1"} if body is not None else {}
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_flow(self):
        s, st = self.req("/api/status")
        self.assertEqual(s, 200)
        self.assertEqual(st["days"][0]["status"], "locked")
        s, _ = self.req("/api/key?day=2026-11-21")
        self.assertEqual(s, 403)  # locked until submitted
        s, p = self.req("/api/paper?day=2026-11-21")
        self.assertNotIn("answer", json.dumps(p["paper"]["questions"][0]))
        s, _ = self.req("/api/draft", {"day": "2026-11-21", "answers": {"1": "A"}, "elapsed_s": 60})
        self.assertEqual(s, 200)
        s, _ = self.req("/api/draft", {"day": "2026-11-21", "answers": {"1": "Z"}})
        self.assertEqual(s, 400)
        s, _ = self.req("/api/submit", {"day": "2026-11-21", "answers": {"1": "A"}}, headers={"X-Aptitude": "0"})
        self.assertEqual(s, 403)
        s, _ = self.req("/api/submit", {"day": "2026-11-21", "answers": {"1": "A"}}, headers={"Origin": "http://evil.example"})
        self.assertEqual(s, 403)
        k = store.key("2026-11-21")["answers"]
        s, r = self.req("/api/submit", {"day": "2026-11-21", "answers": {"1": k["1"]["answer"], "2": "A" if k["2"]["answer"] != "A" else "B"}, "elapsed_s": 3000})
        self.assertEqual(s, 200)
        self.assertEqual(r["attempt"]["totals"]["correct"], 1)
        self.assertEqual(r["attempt"]["totals"]["wrong"], 1)
        self.assertNotIn("check_code", json.dumps(r["key"]))
        s, _ = self.req("/api/submit", {"day": "2026-11-21", "answers": {"1": "A"}})
        self.assertEqual(s, 409)  # one submission
        s, m = self.req("/api/mistakes")
        self.assertEqual(len(m), 49)
        s, pr = self.req("/api/progress")
        self.assertEqual(pr["solved_days"], 1)
        s, h = self.req("/api/health")
        self.assertEqual(h["builds"][0]["published"], 50)

    def test_wrong_host_refused(self):
        r = urllib.request.Request(self.base + "/api/status", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 403)

    def test_bad_day(self):
        s, _ = self.req("/api/paper?day=../../etc")
        self.assertEqual(s, 404)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
