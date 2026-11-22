"""Checks every generated question must pass before it is published.

1. Structure: stem, the right number of distinct options, a valid key, a real explanation,
   no LaTeX, the set it belongs to exists.
2. Code check (maths, data, puzzles, programs): the setter's own code recomputes the answer
   from the givens in a sandbox; the computed value must match the keyed option and only
   that option.
3. Not a repeat: a question whose wording (numbers ignored) overlaps an earlier one is dropped.
4. Blind solve (done in build.py): a separate Claude call answers without seeing the key;
   any disagreement drops the question.
"""
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LETTERS = "ABCDE"
DIFFICULTIES = ("easy", "medium", "hard")
LATEX = re.compile(r"\\(frac|sqrt|times|cdot|left|right|begin|le|ge|neq|pi|theta|alpha)\b|\\\(|\\\[|\$\$")


# ------------------------------------------------------------------ text helpers

def clean(s):
    """House style: em and en dashes become a comma or a hyphen; tidy whitespace."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"[ \t]*\u2014[ \t]*", ", ", s)
    s = re.sub(r"[ \t]*\u2013[ \t]*", "-", s)
    s = re.sub(r",[ \t]*,", ",", s)
    s = s.replace("\r\n", "\n")
    return s.strip()


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s./%-]", " ", str(s).lower())).strip(" .")


def shingles(text, n=3):
    toks = re.findall(r"[a-z]+|\d+(?:\.\d+)?", str(text).lower())
    toks = ["#" if t[0].isdigit() else t for t in toks]  # numbers ignored: a re-skinned question is still a repeat
    if len(toks) < n:
        return set(toks)
    return {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ------------------------------------------------------------------ numbers in options

NUM = r"[-+]?\d+(?:\.\d+)?"


def parse_number_prec(text):
    """(value, tolerance) for the single number an option states ("₹1,250", "25%", "3/4", "2 1/2",
    "-0.5 km"), else (None, None). The tolerance is half a unit of the last digit shown, so
    "2023" allows 2022.5 to 2023.5 and "12.5%" allows 12.45 to 12.55; fractions are exact."""
    if text is None:
        return None, None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text), 1e-9
    s = str(text).replace("\u2212", "-").replace(",", "")
    if re.search(r"[√π^]|sqrt|\be\b|log|sin|cos|tan|\bi\b", s, re.I):
        return None, None
    s = re.sub(r"(₹|rs\.?|inr)\s*", "", s, flags=re.I)
    m = re.fullmatch(r"\D*?(%s)\s+(\d+)\s*/\s*(\d+)\D*" % NUM, s)  # mixed fraction 2 1/2
    if m:
        whole, a, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
        if not b:
            return None, None
        return whole + (a / b if whole >= 0 else -a / b), 1e-9
    toks = re.findall(r"%s(?:\s*/\s*%s)?" % (NUM, NUM), s)
    if len(toks) != 1:
        return None, None
    t = toks[0].replace(" ", "")
    if "/" in t:
        a, b = t.split("/")
        return (float(a) / float(b), 1e-9) if float(b) else (None, None)
    dec = len(t.split(".")[1]) if "." in t else 0
    return float(t), 0.5 * 10 ** (-dec)


def parse_number(text):
    return parse_number_prec(text)[0]


def same_text(s):
    """For spotting duplicate options: case and spacing only, so "m = 18" and "m ≠ 18" stay different."""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def close(a, b, rel):
    return abs(a - b) <= max(1e-9, rel * max(abs(a), abs(b)))


# ------------------------------------------------------------------ sandbox

def run_check(code, timeout=12):
    """Run the setter's check code in a child Python (-I: no user site, no cwd imports)."""
    try:
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "sandbox_runner.py")], input=code,
                           capture_output=True, text=True, timeout=timeout, cwd=HERE)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    line = (r.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        return json.loads(line[0])
    except ValueError:
        return {"ok": False, "error": "runner said: %s" % ((r.stderr or r.stdout or "")[-300:])}


def compare(result, q):
    """Does the computed result pick the keyed option, and only it? Returns (ok, reason)."""
    opts = q["options"]
    k = LETTERS.index(q["answer"])
    keyed = opts[k]
    av = q.get("answer_value")
    if isinstance(result, bool):
        result = str(result)
    if isinstance(result, list):
        result = ", ".join(str(x) for x in result)
    if isinstance(result, str):
        rnum = parse_number(result)
        if rnum is None:
            r = norm(result)
            if len(r) == 1 and r.upper() in LETTERS:
                return False, "check code returned an option letter, not a value"
            if r == norm(keyed) or (isinstance(av, str) and r == norm(av) and norm(av) == norm(keyed)):
                others = [i for i, o in enumerate(opts) if i != k and norm(o) == r]
                return (not others), ("ok" if not others else "two options match the computed answer")
            # a printed program output may be multi-line; compare without whitespace
            if re.sub(r"\s+", "", r) == re.sub(r"\s+", "", norm(keyed)):
                return True, "ok"
            return False, "computed %r but key is %r" % (result[:80], keyed[:80])
        result = rnum
    if not isinstance(result, (int, float)) or (isinstance(result, float) and (math.isnan(result) or math.isinf(result))):
        return False, "computed a non-number: %r" % (result,)
    r = float(result)
    if isinstance(av, (int, float)) and not isinstance(av, bool) and not close(r, float(av), 1e-4):
        return False, "computed %.6g but stated answer value is %.6g" % (r, av)
    parsed = [parse_number_prec(o) for o in opts]
    # one precision for the whole question: if any option shows 2 decimals, "6" means exactly 6.00
    dec_tols = [t for v, t in parsed if v is not None and t > 1e-6]
    common = min(dec_tols) if dec_tols else 1e-9
    parsed = [(v, (t if t <= 1e-6 else common)) if v is not None else (None, None) for v, t in parsed]
    vals = [v for v, _ in parsed]
    kv, ktol = parsed[k]
    if kv is None:
        if isinstance(av, (int, float)) and not isinstance(av, bool):
            return True, "ok (keyed option is not a plain number; matched the stated value)"
        return False, "keyed option is not a plain number and no answer_value was given"
    if abs(r - kv) > ktol + 1e-9 * max(1.0, abs(r)):
        return False, "computed %.6g but keyed option says %s" % (r, keyed[:60])
    rivals = [i for i, (v, tol) in enumerate(parsed) if i != k and v is not None and abs(r - v) <= tol + 1e-9 * max(1.0, abs(r))]
    if rivals:
        return False, "computed value also matches option %s" % LETTERS[rivals[0]]
    nearest = min((abs(v - r), i) for i, v in enumerate(vals) if v is not None)[1]
    if nearest != k:
        return False, "another option is closer to the computed value"
    return True, "ok"


# ------------------------------------------------------------------ structure

def structure(q, sec_cfg, group_keys):
    """Return a list of problems (empty = fine)."""
    probs = []
    n_opt = sec_cfg["options"]
    if not isinstance(q.get("stem"), str) or len(q["stem"].strip()) < 12:
        probs.append("stem missing or too short")
    opts = q.get("options")
    if not isinstance(opts, list) or len(opts) != n_opt or not all(isinstance(o, str) and o.strip() for o in opts):
        probs.append("needs exactly %d non-empty options" % n_opt)
    elif len({same_text(o) for o in opts}) != len(opts):
        probs.append("two options are the same")
    if q.get("answer") not in LETTERS[:n_opt]:
        probs.append("answer letter invalid")
    if not isinstance(q.get("explanation"), str) or len(q["explanation"].strip()) < 40:
        probs.append("explanation missing or too short")
    if q.get("difficulty") not in DIFFICULTIES:
        probs.append("difficulty missing")
    text = " ".join([str(q.get("stem", ""))] + [str(o) for o in (opts or [])])
    if LATEX.search(text):
        probs.append("uses LaTeX")
    if sec_cfg.get("code_check") == "required" and not (q.get("check") or "").strip():
        probs.append("check code required for this section")
    g = q.get("group")
    if g and g not in group_keys:
        probs.append("refers to a set that does not exist")
    return probs


def group_problems(g, kind):
    probs = []
    if not isinstance(g.get("text"), str) or len(g["text"].strip()) < 60:
        if not (g.get("table") or g.get("chart")):
            probs.append("set has no text, table or chart")
    if kind == "RC" and len((g.get("text") or "").split()) < 250:
        probs.append("passage shorter than 250 words")
    t = g.get("table")
    if t is not None:
        cols, rows = t.get("columns"), t.get("rows")
        if not (isinstance(cols, list) and isinstance(rows, list) and rows and all(isinstance(r, list) and len(r) == len(cols) for r in rows)):
            probs.append("table rows do not match its columns")
    c = g.get("chart")
    if c is not None:
        labels, series = c.get("labels"), c.get("series")
        if c.get("type") not in ("bar", "line", "pie") or not isinstance(labels, list) or not isinstance(series, list) or not series:
            probs.append("chart is malformed")
        else:
            for s in series:
                vals = s.get("values") if isinstance(s, dict) else None
                if not isinstance(vals, list) or len(vals) != len(labels) or not all(isinstance(v, (int, float)) for v in vals):
                    probs.append("chart series does not match its labels")
                    break
    return probs


def is_repeat(text, past_shingles, threshold):
    s = shingles(text)
    best = 0.0
    for p in past_shingles:
        j = jaccard(s, p)
        if j > best:
            best = j
            if best >= threshold:
                break
    return best >= threshold, best
