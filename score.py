"""Scoring and progress: grade one attempt, read answers typed in chat, roll up the stats."""
import re
from datetime import date, timedelta

LETTERS = "ABCDE"


def parse_answers(text, paper):
    """Answers as typed in chat: "1B 2C 3D", "1-b, 2-c", "1. A 2) B", or one letter per
    question in order ("BCDA-AB...", "B C D A", where - . x or ? marks a skip).
    Returns ({"1": "B", ...}, [problems])."""
    n = len(paper["questions"])
    opts = {str(q["n"]): len(q["options"]) for q in paper["questions"]}
    up = (text or "").upper()
    pairs = re.findall(r"(?<!\d)(\d{1,2})\s*[).:=\-]?\s*([A-E])(?![A-Z])", up)
    out, probs = {}, []
    if pairs:
        for num, letter in pairs:
            if num not in opts:
                probs.append("there is no question %s" % num)
                continue
            if LETTERS.index(letter) >= opts[num]:
                probs.append("question %s has only %d options, so %s is not one" % (num, opts[num], letter))
                continue
            if num in out and out[num] != letter:
                probs.append("question %s was answered twice (%s and %s); kept %s" % (num, out[num], letter, letter))
            out[num] = letter
    else:
        toks = re.findall(r"[A-E]|[-.X?_]", re.sub(r"\s+", "", up))
        if len(toks) != n:
            probs.append("found %d answers for %d questions; send them as 1B 2C 3D ... so none is misplaced" % (len(toks), n))
            return {}, probs
        for i, t in enumerate(toks, 1):
            if t in LETTERS:
                if LETTERS.index(t) >= opts[str(i)]:
                    probs.append("question %d has only %d options, so %s is not one" % (i, opts[str(i)], t))
                    continue
                out[str(i)] = t
    return out, probs


def grade(paper, key, answers, scoring, mode, elapsed_s=None, submitted_at=None):
    """Score one attempt. `answers` maps question number (str) to a letter; missing means skipped."""
    res, sections, topics = {}, {}, {}
    tot = {"correct": 0, "wrong": 0, "skipped": 0}
    for q in paper["questions"]:
        n = str(q["n"])
        k = key["answers"][n]["answer"]
        a = (answers or {}).get(n) or ""
        r = "skipped" if not a else ("correct" if a == k else "wrong")
        res[n] = r
        tot[r] += 1
        s = sections.setdefault(q["section"], {"correct": 0, "wrong": 0, "skipped": 0, "total": 0})
        s[r] += 1
        s["total"] += 1
        t = topics.setdefault("%s|%s" % (q["section"], q["topic"]), {"correct": 0, "wrong": 0, "skipped": 0})
        t[r] += 1

    def roll(d):
        att = d["correct"] + d["wrong"]
        d["attempted"] = att
        d["accuracy"] = round(d["correct"] / float(att), 3) if att else None
        d["net"] = round(d["correct"] * scoring["correct"] + d["wrong"] * scoring["wrong"] + d["skipped"] * scoring["skipped"], 2)
        return d

    tot["total"] = len(paper["questions"])
    for d in [tot] + list(sections.values()) + list(topics.values()):
        roll(d)
    return {"day": paper["day"], "mode": mode, "submitted_at": submitted_at, "elapsed_s": elapsed_s,
            "answers": {k: v for k, v in (answers or {}).items() if v}, "results": res,
            "totals": tot, "sections": sections, "topics": topics}


def self_check(paper, submitted_at):
    """Solved on paper, only the key wanted: unlock without a score."""
    return {"day": paper["day"], "mode": "self-check", "submitted_at": submitted_at, "elapsed_s": None,
            "answers": {}, "results": {}, "totals": None, "sections": {}, "topics": {}}


def scored(att):
    return bool(att and att.get("totals") and att["totals"].get("attempted"))


def streak(solved_days, today):
    """Consecutive days with a submitted set, ending today (or yesterday if today is still open)."""
    s = set(solved_days)
    d = date.fromisoformat(today)
    if d.isoformat() not in s:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in s:
        n += 1
        d -= timedelta(days=1)
    return n


def rollup(day_rows, today, window=14):
    """day_rows: [(day, paper, attempt)], newest first. Section and topic accuracy for planning and the page."""
    sections, topics, days = {}, {}, []
    cutoff = (date.fromisoformat(today) - timedelta(days=window)).isoformat()
    for day, paper, att in day_rows:
        days.append({"day": day, "solved": bool(att), "mode": (att or {}).get("mode"),
                     "totals": (att or {}).get("totals"), "sections": (att or {}).get("sections", {}),
                     "elapsed_s": (att or {}).get("elapsed_s"), "phase": (paper or {}).get("phase")})
        if not scored(att):
            continue
        for code, s in att["sections"].items():
            agg = sections.setdefault(code, {"correct": 0, "wrong": 0, "skipped": 0, "recent": []})
            if day >= cutoff:
                for k in ("correct", "wrong", "skipped"):
                    agg[k] += s[k]
            if len(agg["recent"]) < 3 and s.get("attempted"):
                agg["recent"].append(s["accuracy"])
        for tk, t in att["topics"].items():
            code, topic = tk.split("|", 1)
            agg = topics.setdefault(tk, {"section": code, "topic": topic, "correct": 0, "wrong": 0, "skipped": 0, "last": day})
            for k in ("correct", "wrong", "skipped"):
                agg[k] += t[k]
            agg["last"] = max(agg["last"], day)
    for agg in list(sections.values()) + list(topics.values()):
        att = agg["correct"] + agg["wrong"]
        agg["attempted"] = att
        agg["accuracy"] = round(agg["correct"] / float(att), 3) if att else None
    solved = [d["day"] for d in days if d["solved"]]
    return {"sections": sections, "topics": topics, "days": days, "streak": streak(solved, today),
            "solved_days": len(solved)}
