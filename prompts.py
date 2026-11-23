"""Prompts for the question setter and for the blind solver, and turning replies into questions."""
import json
import random

from verify import clean, LETTERS

SETTER = """You write original practice questions for two Indian MBA entrance exams: XAT (XLRI) and the BITS Pilani BAAT (Business Analytics Aptitude Test). You are an experienced paper setter.

Rules:
- Every question is original. Never copy or lightly edit questions from past papers, coaching material or books.
- Exactly one option is correct. Wrong options come from real mistakes: a wrong formula, a skipped step, a misread condition, a reversed ratio, a plausible but flawed judgement.
- Difficulty is honest. easy: one or two steps. medium: typical of the exam. hard: the top 10% of the exam, two ideas combined or a trap that careful reading avoids.
- Every question must be fully answerable from what it states. No missing data, no trick wording.
- Plain text only. Maths uses Unicode (x², √3, π, ≤, ≥, ×, ÷) or plain forms like x^2 and sqrt(3). Never LaTeX, never dollar signs. Money in rupees (₹).
- Never use em dashes or en dashes. Use commas, colons or hyphens.
- Options are given without letters; the page adds them (A is the first option in your list). Put the correct option at the position named for each slot, and write the explanation for that final order.
- Explanations teach: step by step, plain words, every step shown, at most 150 words. Add a shortcut when a faster exam method exists, else leave it empty.
- Check code, when the section asks for it: self-contained Python that uses only math, fractions, itertools, functools, statistics, decimal, collections, string, operator, heapq and bisect. It recomputes the answer from the givens in the question (never copy the answer) and stores it in a variable named result. result is in the same unit and form as the options: 25 for "25%", 1250 for "₹1,250", 2.5 for "2.5 lakh", or the exact option text for a non-numeric answer. For a logic puzzle, brute-force every arrangement, keep those that satisfy every clue, assert that exactly one remains, then compute result from it. For a "what does this program print" question the check code is the program itself, and it may print.
- answer_value: the correct answer as a number (same unit as the options) when it is numeric, the exact option text when it is not.
- Reply with one JSON object only, no prose before or after it."""

SHAPE = """JSON shape:
{"groups": [{"key": "<set key given above>", "kind": "<set kind>", "title": "<4-8 word title>",
             "text": "<passage, caselet, puzzle clues or a one-line introduction>",
             "table": null or {"columns": ["...", "..."], "rows": [["...", 12, 3.5], ...]},
             "chart": null or {"type": "bar" or "line" or "pie", "title": "...", "unit": "...", "labels": ["...", ...],
                               "series": [{"name": "...", "values": [numbers, one per label]}]}}],
 "questions": [{"slot": <slot number>, "group": "<set key>" or null, "topic": "<topic exactly as given>",
                "subtopic": "<question type, e.g. inference, weaken, remainder theorem>",
                "difficulty": "easy" or "medium" or "hard", "stem": "...", "options": ["...", ...],
                "answer": "<letter>", "answer_value": <number or option text>,
                "explanation": "...", "shortcut": "...", "check": "<python or empty string>"}]}"""

CHECK_RULES = {
    "required": "Every question MUST include check code. A question without it is thrown away.",
    "optional": "Include check code for every question whose answer can be computed (numbers, puzzles with clues, programs, Boolean algebra, data-structure operations). Leave it empty only for pure judgement or concept questions.",
    "none": "Leave check empty.",
}

SET_RULES = {
    "DI": "For each set, put the data in table or chart (real numbers you invent, never a real dataset) and a one or two line introduction in text; for a caselet set put all the data in text and leave table and chart null. Check code must define the data itself (copy the numbers) and compute from it.",
    "RC": "For each set, text is the passage (380 to 480 words, in paragraphs separated by a blank line). table and chart are null.",
    "LR": "For each set, text holds the setup and every clue, numbered. The clues must give exactly one arrangement; the check code proves it by brute force.",
    "DM": "For each set, text is the caselet (150 to 250 words) with named people, goals and constraints. table and chart are null unless the case needs a small table of numbers.",
}


def section_prompt(code, sec, sec_plan, avoid, existing_groups=None, start_slot=1):
    """The setter's brief for one section, and {slot: what the plan wants there}.
    existing_groups: [(group, stems already asked, how many more, difficulties)] for a top-up."""
    lines = ["SECTION: %s, %s (exams: %s)" % (code, sec["name"], " and ".join(sec["exams"])),
             "STYLE: " + sec["guide"],
             "OPTIONS: every question has exactly %d options (the page labels them %s to %s)." % (sec["options"], LETTERS[0], LETTERS[sec["options"] - 1]),
             "CHECK CODE: " + CHECK_RULES[sec["code_check"]], ""]
    slot = start_slot
    slots = {}
    # where each correct answer goes: an even spread over the letters (models otherwise favour A)
    rng = random.Random("positions-%s-%d" % (code, start_slot))
    bag = []

    def position():
        if not bag:
            bag.extend(LETTERS[:sec["options"]])
            rng.shuffle(bag)
        return bag.pop()

    def take(k, group, topic, diffs):
        nonlocal slot
        nums, at = [], []
        for i in range(k):
            p = position()
            slots[slot] = {"group": group, "topic": topic, "difficulty": diffs[i] if i < len(diffs) else "medium", "answer_at": p}
            nums.append(str(slot))
            at.append(p)
            slot += 1
        return ", ".join(nums) + " (correct options in order: %s)" % ", ".join(at)

    if existing_groups:
        lines.append("SETS THAT ALREADY EXIST (write new questions on them and reuse their key; leave them out of your groups list; do not repeat the questions already asked):")
        for g, asked, k, diffs in existing_groups:
            lines.append("- key %s: %s" % (g["key"], json.dumps({x: g.get(x) for x in ("kind", "title", "text", "table", "chart")}, ensure_ascii=False)))
            lines.append("  already asked: %s" % json.dumps(asked, ensure_ascii=False))
            nums = take(k, g["key"], g["kind"], diffs)
            lines.append("  write %d more question(s) on this set, difficulties: %s, slots %s" % (k, ", ".join(diffs), nums))
        lines.append("")
    if sec_plan["groups"]:
        lines.append("SETS TO WRITE (" + SET_RULES.get(code, "") + "):")
        for g in sec_plan["groups"]:
            nums = take(g["ask"], g["key"], g["kind"], g["difficulties"])
            d = g["difficulties"]
            lvl = max(("easy", "medium", "hard"), key=lambda x: (d.count(x), -("easy", "medium", "hard").index(x)))
            lines.append("- key %s: a %s set with %d questions, pitched at an overall %s level (the passage, case, puzzle or data too), question difficulties: %s, slots %s" % (
                g["key"], g["kind"], g["ask"], lvl, ", ".join(d), nums))
        lines.append("")
    if sec_plan["slots"]:
        lines.append("STANDALONE QUESTIONS TO WRITE (group null):")
        for s in sec_plan["slots"]:
            n = take(1, None, s["topic"], [s["difficulty"]])
            num, at = n.split(" (correct options in order: ")
            lines.append("- slot %s: topic \"%s\", difficulty %s, correct option %s" % (num, s["topic"], s["difficulty"], at.rstrip(")")))
        lines.append("")
    if avoid:
        lines.append("RECENT QUESTIONS TO AVOID (write different ideas, not the same idea with new numbers):")
        lines += ["- " + a for a in avoid]
        lines.append("")
    lines.append("Write exactly %d questions in total, one per slot." % len(slots))
    lines.append(SHAPE)
    return "\n".join(lines), slots


def parse_setter(reply, code, sec_plan, slots, existing_groups=None):
    """Turn the setter's JSON into (groups, questions). Topic, set and difficulty come from the plan's slot."""
    obj = reply if isinstance(reply, dict) else {}
    plan_groups = {g["key"]: g for g in sec_plan["groups"]}
    groups = {}
    for g in obj.get("groups") or []:
        if not isinstance(g, dict) or g.get("key") not in plan_groups:
            continue
        groups[g["key"]] = {"key": g["key"], "section": code, "kind": plan_groups[g["key"]]["kind"],
                            "title": clean(g.get("title") or ""), "text": clean(g.get("text") or ""),
                            "table": g.get("table") or None, "chart": g.get("chart") or None}
    known = set(groups) | {eg[0]["key"] for eg in (existing_groups or [])}
    qs, used = [], set()
    for q in obj.get("questions") or []:
        if not isinstance(q, dict):
            continue
        try:
            s = int(q.get("slot"))
        except (TypeError, ValueError):
            s = None
        if s not in slots or s in used:
            continue  # unplanned or duplicate slot: ignore rather than guess its topic
        used.add(s)
        want = slots[s]
        g = want["group"]
        diff = q.get("difficulty") if q.get("difficulty") in ("easy", "medium", "hard") else want["difficulty"]
        qs.append({"section": code, "slot": s, "group": g if (g is None or g in known) else "?" + g,
                   "topic": want["topic"], "subtopic": clean(str(q.get("subtopic") or "")),
                   "difficulty": diff, "stem": clean(q.get("stem") or ""),
                   "options": [clean(str(o)) for o in (q.get("options") or [])],
                   "answer": str(q.get("answer") or "").strip().upper()[:1],
                   "answer_value": q.get("answer_value"),
                   "explanation": clean(q.get("explanation") or ""), "shortcut": clean(q.get("shortcut") or ""),
                   "check": q.get("check") or "", "_target": want.get("answer_at")})
    return groups, qs


SOLVER = """You are a top scorer in XAT and the BITS BAAT. Solve each multiple-choice question yourself, carefully and independently. Do not assume any option is right; work it out, then pick.
For Decision Making, pick the option a careful XLRI panel would choose: it solves the problem, respects every constraint and stakeholder, and is ethical and practical.
If a question is flawed (no correct option, more than one correct option, missing data, ambiguous wording), say so in flaw; otherwise leave flaw empty.
Reply with one JSON object only:
{"answers": [{"id": "<id>", "work": "<key steps, at most 40 words>", "answer": "<letter>", "confidence": <1-5>, "flaw": ""}]}"""


def render_group(g):
    parts = ["SET %s (%s)%s" % (g["key"], g["kind"], (": " + g["title"]) if g.get("title") else "")]
    if g.get("text"):
        parts.append(g["text"])
    t = g.get("table")
    if t:
        parts.append("Table: " + " | ".join(str(c) for c in t["columns"]))
        parts += ["  " + " | ".join(str(c) for c in r) for r in t["rows"]]
    c = g.get("chart")
    if c:
        parts.append("Chart (%s): %s%s" % (c.get("type"), c.get("title", ""), (" [unit: %s]" % c["unit"]) if c.get("unit") else ""))
        parts.append("  labels: " + ", ".join(str(x) for x in c["labels"]))
        for s in c["series"]:
            parts.append("  %s: %s" % (s.get("name", "values"), ", ".join(str(v) for v in s["values"])))
    return "\n".join(parts)


def solve_prompt(groups, questions):
    out = []
    seen = set()
    for q in questions:
        g = q.get("group")
        if g and g not in seen and g in groups:
            out.append(render_group(groups[g]))
            out.append("")
            seen.add(g)
        out.append("QUESTION id=%s%s" % (q["_id"], (" (set %s)" % g) if g else ""))
        out.append(q["stem"])
        for i, o in enumerate(q["options"]):
            out.append("%s. %s" % (LETTERS[i], o))
        out.append("")
    out.append("Answer all %d questions." % len(questions))
    return "\n".join(out)
