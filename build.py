"""Build one day's set: plan, write, check, blind-solve, top up, publish.

Per section (all sections run in parallel):
  1. the setter (Claude) writes the planned questions plus a spare or two;
  2. each question passes the structure check, the repeat check and, where it has one,
     its own check code run in the sandbox (the computed answer must pick the keyed option);
  3. a separate Claude call solves the survivors blind; any disagreement drops the question;
  4. missing questions are written again (up to `topup_rounds` more rounds).
Then the paper (no answers) and the key are written to separate files.
"""
import math
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import llm
import plan as planmod
import prompts
import score
import store
import verify


def stats_now(today):
    rows = [(d, store.paper(d), store.attempt(d)) for d in store.days()]
    return score.rollup(rows, today)


class Builder:
    def __init__(self, cfg, day, call=None, log=None):
        self.cfg = cfg
        self.day = day
        self.call = call or llm.call
        self.log = log or store.log
        self.lock = threading.Lock()
        self.accepted = {}   # section -> [question]
        self.groups = {}     # key -> group (kept sets)
        self.rejected = []
        self.notes = []
        self.calls = 0
        self.progress = {}
        bank = store.bank()
        self.bank = bank
        self.past_stems, self.past_groups = {}, []
        for r in bank:
            if r.get("type") == "group":
                self.past_groups.append(verify.shingles((r.get("title") or "") + " " + (r.get("text") or "")))
            else:
                self.past_stems.setdefault(r.get("section"), []).append(verify.shingles(r.get("stem", "")))
        self.today_stems = {}

    # ---------------------------------------------------------------- progress
    def set_progress(self, code, msg):
        with self.lock:
            self.progress[code] = msg
            rs = store.load(store.state("run.json"), {}) or {}
            rs["sections"] = dict(self.progress)
            store.save(store.state("run.json"), rs)

    def reject(self, q, why, stage):
        with self.lock:
            self.rejected.append({"section": q.get("section"), "topic": q.get("topic"), "stage": stage,
                                  "why": why if isinstance(why, str) else "; ".join(why),
                                  "stem": (q.get("stem") or "")[:160]})

    # ---------------------------------------------------------------- calls
    def ask(self, system, prompt, model, what):
        last = ""
        for attempt in range(2):
            try:
                with self.lock:
                    self.calls += 1
                t0 = time.time()
                text = self.call(system, prompt, model, self.cfg["models"]["timeout_s"], store.path("work"))
                obj = llm.first_json(text, "{")
                self.log("%s: reply in %ds" % (what, time.time() - t0))
                return obj
            except Exception as e:  # noqa: BLE001
                last = "%s: %s" % (type(e).__name__, str(e)[:200])
                self.log("%s: attempt %d failed (%s)" % (what, attempt + 1, last))
        return None

    def avoid(self, code, k=25):
        rows = [r for r in self.bank if r.get("section") == code]
        out = []
        for r in rows[-k:]:
            if r.get("type") == "group":
                out.append("set (%s): %s" % (r.get("kind", ""), r.get("title", "")))
            else:
                out.append("%s: %s" % (r.get("topic", ""), (r.get("stem") or "")[:110].replace("\n", " ")))
        return out

    # ---------------------------------------------------------------- one section, one round
    def section_round(self, sp, rnd, existing=None):
        code = sp["code"]
        sec = self.cfg["sections"][code]
        self.set_progress(code, "writing (round %d)" % (rnd + 1))
        prompt, slots = prompts.section_prompt(code, sec, sp, self.avoid(code), existing, start_slot=1 + 100 * rnd)
        reply = self.ask(prompts.SETTER, prompt, self.cfg["models"]["generate"], "%s round %d setter" % (code, rnd + 1))
        if reply is None:
            self.set_progress(code, "setter failed (round %d)" % (rnd + 1))
            return
        groups, qs = prompts.parse_setter(reply, code, sp, slots, existing)
        for q in qs:
            q["_id"] = "%s-%d-%s" % (code, rnd, q["slot"])
        self.set_progress(code, "checking %d questions (round %d)" % (len(qs), rnd + 1))
        groups, cands = self.check(code, sec, groups, qs)
        if cands:
            self.set_progress(code, "blind-solving %d questions (round %d)" % (len(cands), rnd + 1))
            self.solve(code, cands)
        with self.lock:
            have = len(self.accepted.get(code, []))
        self.set_progress(code, "%d of %d ready" % (min(have, sp["n"]), sp["n"]))

    def check(self, code, sec, groups, qs):
        thr = self.cfg["verify"]["dedup_threshold"]
        gthr = self.cfg["verify"]["group_dedup_threshold"]
        kept = {}
        for k, g in groups.items():
            probs = verify.group_problems(g, code)
            body = (g.get("title") or "") + " " + (g.get("text") or "")
            if not probs and len(body.split()) >= 40:
                rep, sim = verify.is_repeat(body, self.past_groups, gthr)
                if rep:
                    probs.append("set repeats an earlier one (%d%% overlap)" % round(sim * 100))
            if probs:
                for q in qs:
                    if q.get("group") == k:
                        q["_set_rejected"] = "; ".join(probs)
                self.reject({"section": code, "topic": g.get("kind"), "stem": "SET " + (g.get("title") or k)}, probs, "set")
            else:
                kept[k] = g
        with self.lock:
            known = set(self.groups) | set(kept)
        out = []
        for q in qs:
            if q.get("_set_rejected"):
                continue
            probs = verify.structure(q, sec, known)
            if not probs:
                with self.lock:
                    pool = self.past_stems.get(code, []) + self.today_stems.get(code, [])
                rep, sim = verify.is_repeat(q["stem"], pool, thr)
                if rep:
                    probs.append("repeats an earlier question (%d%% overlap)" % round(sim * 100))
            if not probs and q["check"].strip():
                r = verify.run_check(q["check"])
                if not r.get("ok"):
                    if sec["code_check"] == "required":
                        probs.append("check code failed: %s" % r.get("error", "")[:160])
                    else:
                        q["_code"] = "error"  # fall back to the blind solve alone
                else:
                    ok, why = verify.compare(r.get("result"), q)
                    if ok:
                        q["_code"] = "pass"
                    else:
                        probs.append("check code disagrees with the key: " + why)
            if probs:
                self.reject(q, probs, "check")
            else:
                out.append(q)
        if kept:
            with self.lock:
                self.groups.update(kept)
        return kept, out

    def solve(self, code, cands):
        with self.lock:
            groups = dict(self.groups)
        reply = self.ask(prompts.SOLVER, prompts.solve_prompt(groups, cands), self.cfg["models"]["solve"],
                         "%s blind solve (%d)" % (code, len(cands)))
        answers = {}
        if reply is not None:
            answers = {str(a.get("id")): a for a in (reply.get("answers") or []) if isinstance(a, dict)}
        for q in cands:
            a = answers.get(q["_id"])
            if a is None:
                if reply is None and q.get("_code") == "pass":
                    q["_solver"] = "unavailable"
                    self.accept(code, q)
                    with self.lock:
                        self.notes.append("%s: blind solve failed; kept because its check code passed" % q["_id"])
                else:
                    self.reject(q, "the blind solver did not answer it", "solve")
                continue
            ans = str(a.get("answer") or "").strip().upper()[:1]
            flaw = str(a.get("flaw") or "").strip()
            conf = a.get("confidence")
            if ans != q["answer"]:
                self.reject(q, "blind solve picked %s, key says %s%s" % (ans or "nothing", q["answer"],
                                                                      (" (flaw: %s)" % flaw[:120]) if flaw else ""), "solve")
                continue
            if flaw and not (isinstance(conf, (int, float)) and conf >= 4):
                self.reject(q, "the solver agreed but flagged a flaw: " + flaw[:160], "solve")
                continue
            if flaw:
                with self.lock:
                    self.notes.append("%s: solver note: %s" % (q["_id"], flaw[:160]))
            q["_solver"] = "agree"
            q["_work"] = str(a.get("work") or "")[:400]
            self.accept(code, q)

    def accept(self, code, q):
        with self.lock:
            self.accepted.setdefault(code, []).append(q)
            self.today_stems.setdefault(code, []).append(verify.shingles(q["stem"]))

    # ---------------------------------------------------------------- top-up
    def topup(self, sp, rnd):
        """What a section still needs: (plan for new sets and slots, existing sets to extend) or None."""
        code = sp["code"]
        with self.lock:
            acc = list(self.accepted.get(code, []))
            groups = dict(self.groups)
        if len(acc) >= sp["n"]:
            return None
        rng = __import__("random").Random("topup-%s-%s-%d" % (self.day, code, rnd))
        new_groups, existing = [], []
        for g in sp["groups"]:
            have = [q for q in acc if q.get("group") == g["key"]]
            short = g["size"] - len(have)
            if short <= 0:
                continue
            diffs = planmod.spread(short + 1, sp.get("difficulty_mix") or planmod.BASE_MIX, rng)
            if g["key"] in groups:
                existing.append((groups[g["key"]], [q["stem"][:200] for q in have], short + 1, diffs))
            else:
                new_groups.append(dict(g, ask=g["size"] + 1, difficulties=planmod.spread(g["size"] + 1, sp.get("difficulty_mix") or planmod.BASE_MIX, rng)))
        alone_n = sp["n"] - sum(g["size"] for g in sp["groups"])
        have_alone = [q for q in acc if not q.get("group")]
        short = alone_n - len(have_alone)
        slots = []
        if short > 0:
            with self.lock:
                missed = [r["topic"] for r in self.rejected if r["section"] == code and r["stage"] != "set"
                          and r["topic"] in self.cfg["sections"][code].get("topics", [])]
            topics = [s["topic"] for s in sp["slots"]]
            done = {q["topic"] for q in have_alone}
            pool = [t for t in missed if t not in done] + [t for t in topics if t not in done] + topics
            sdiffs = planmod.spread(short + 1, sp.get("difficulty_mix") or planmod.BASE_MIX, rng)
            for i in range(short + 1):
                slots.append({"topic": pool[i % len(pool)] if pool else "mixed", "difficulty": sdiffs[i]})
        if not (new_groups or existing or slots):
            return None
        return dict(sp, groups=new_groups, slots=slots), existing

    # ---------------------------------------------------------------- whole day
    def run(self, sections=None):
        t0 = time.time()
        stats = stats_now(self.day)
        p = planmod.make_plan(self.cfg, self.day, stats, self.bank)
        if sections:
            p["sections"] = [s for s in p["sections"] if s["code"] in sections]
        self.plan = p
        workers = max(1, int(self.cfg["models"]["parallel_calls"]))
        self.log("plan: %s" % ", ".join("%s %d" % (s["code"], s["n"]) for s in p["sections"]))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda sp: self.section_round(sp, 0), p["sections"]))
        rounds = 1
        for rnd in range(1, int(self.cfg["verify"]["topup_rounds"]) + 1):
            needs = [(sp, self.topup(sp, rnd)) for sp in p["sections"]]
            needs = [(sp, n) for sp, n in needs if n]
            if not needs:
                break
            rounds += 1
            self.log("top-up round %d: %s" % (rnd, ", ".join(sp["code"] for sp, _ in needs)))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda x: self.section_round(x[1][0], rnd, x[1][1]), needs))
        return self.assemble(p, time.time() - t0, rounds)

    def assemble(self, p, seconds, rounds):
        questions, groups_out, key = [], {}, {}
        sections_out = []
        for sp in p["sections"]:
            code = sp["code"]
            sec = self.cfg["sections"][code]
            acc = self.accepted.get(code, [])
            chosen_sets = []
            for g in sp["groups"]:
                inset = [q for q in acc if q.get("group") == g["key"]]
                chosen_sets.append((g, inset[:g["size"]], inset[g["size"]:]))
            room = sp["n"] - sum(len(c) for _, c, _ in chosen_sets)
            alone = [q for q in acc if not q.get("group")]
            pick, seen_t = [], set()
            for q in alone:  # distinct topics first
                if len(pick) < room and q["topic"] not in seen_t:
                    pick.append(q)
                    seen_t.add(q["topic"])
            for q in alone:
                if len(pick) < room and q not in pick:
                    pick.append(q)
            room -= len(pick)
            for g, c, spare in chosen_sets:  # still short: use a set's spare question
                while room > 0 and spare:
                    c.append(spare.pop(0))
                    room -= 1
            first = len(questions) + 1
            for q in pick + [q for _, c, _ in chosen_sets for q in c]:
                questions.append(q)
            for g, c, _ in chosen_sets:
                if c and g["key"] in self.groups:
                    groups_out[g["key"]] = dict(self.groups[g["key"]])
            count = len(questions) - first + 1
            if count:
                sections_out.append({"code": code, "name": sec["name"], "exams": sec["exams"], "count": count,
                                     "first": first, "last": len(questions), "planned": sp["n"]})
        paper_qs = []
        for i, q in enumerate(questions, 1):
            paper_qs.append({"n": i, "section": q["section"], "group": q.get("group"), "topic": q["topic"],
                             "subtopic": q.get("subtopic", ""), "difficulty": q["difficulty"], "stem": q["stem"],
                             "options": q["options"], "exams": self.cfg["sections"][q["section"]]["exams"]})
            key[str(i)] = {"answer": q["answer"], "explanation": q["explanation"], "shortcut": q.get("shortcut", ""),
                           "checks": {"code": q.get("_code"), "solver": q.get("_solver")},
                           "solver_work": q.get("_work", ""), "check_code": q.get("check", ""),
                           "answer_value": q.get("answer_value")}
            if q.get("group") in groups_out:
                groups_out[q["group"]].setdefault("qnums", []).append(i)
        n = len(paper_qs)
        mpq = float(self.cfg.get("minutes_per_question", 2.2))
        paper = {"day": self.day, "phase": p["phase"], "phase_label": p["phase_label"], "level": p.get("level"),
                 "level_label": p.get("level_label"), "generated_at": store.now(),
                 "suggested_minutes": int(5 * round(n * mpq / 5.0)), "scoring": self.cfg["scoring"],
                 "sections": sections_out, "groups": groups_out, "questions": paper_qs}
        by_stage = {}
        for r in self.rejected:
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
        code_checked = sum(1 for q in questions if q.get("_code") == "pass")
        meta = {"day": self.day, "built_at": store.now(), "seconds": int(seconds), "rounds": rounds, "claude_calls": self.calls,
                "planned": sum(s["n"] for s in p["sections"]), "published": n, "code_checked": code_checked,
                "solver_agreed": sum(1 for q in questions if q.get("_solver") == "agree"),
                "rejected": len(self.rejected), "rejected_by_stage": by_stage, "rejections": self.rejected,
                "notes": self.notes, "plan": p, "models": self.cfg["models"]}
        return paper, {"day": self.day, "answers": key}, meta


def publish(paper, key, meta, force=False):
    day = paper["day"]
    d = store.day_dir(day)
    with store.lock():
        if os.path.exists(os.path.join(d, "paper.json")):
            if store.unlocked(day):
                raise RuntimeError("%s is already solved; not replacing it" % day)
            if not force:
                raise RuntimeError("%s already has a set (use --force to replace it)" % day)
            old = os.path.join(d, "_replaced-%s" % time.strftime("%H%M%S"))
            os.makedirs(old)
            for f in ("paper.json", "key.json", "meta.json", "draft.json"):
                if os.path.exists(os.path.join(d, f)):
                    shutil.move(os.path.join(d, f), os.path.join(old, f))
        store.save(os.path.join(d, "key.json"), key)
        store.save(os.path.join(d, "meta.json"), meta)
        store.save(os.path.join(d, "paper.json"), paper)  # written last: its presence means "ready"
        rows = []
        for g in paper["groups"].values():
            rows.append({"type": "group", "day": day, "section": g["section"], "kind": g["kind"],
                         "title": g.get("title", ""), "text": (g.get("text") or "")[:3000]})
        for q in paper["questions"]:
            rows.append({"type": "q", "day": day, "section": q["section"], "topic": q["topic"], "n": q["n"],
                         "stem": q["stem"][:600]})
        store.append_bank(rows)


def minutes(paper):
    return paper.get("suggested_minutes") or int(math.ceil(len(paper["questions"]) * 2.2))
