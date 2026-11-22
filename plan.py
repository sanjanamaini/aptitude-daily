"""Today's blueprint: which sections, sets, topics and difficulties the 50 questions cover.

- The phase (XAT focus until 3 Jan 2027, then BAAT focus) sets the section mix (config.json).
- Topics are drawn with weights: weaker topics (by your accuracy) and topics not seen for a while
  come up more often, so every topic keeps returning and weak ones return sooner.
- Difficulty follows your last three scores in that section, or the warm-up ramp while one runs.
- A fixed random seed per day: the same day always gives the same plan.
"""
import math
import random
from datetime import date

SECTION_ORDER = ["VA", "RC", "DM", "QA", "DI", "LR", "AA"]
BASE_MIX = {"easy": 0.25, "medium": 0.5, "hard": 0.25}
HARD_MIX = {"easy": 0.1, "medium": 0.45, "hard": 0.45}
EASY_MIX = {"easy": 0.4, "medium": 0.45, "hard": 0.15}


def phase_for(cfg, day):
    for ph in cfg["phases"]:
        if ph["until"] is None or day < ph["until"]:
            return ph
    return cfg["phases"][-1]


def difficulty_mix(recent):
    if len(recent) >= 2:
        m = sum(recent) / float(len(recent))
        if m >= 0.8:
            return HARD_MIX
        if m <= 0.45:
            return EASY_MIX
    return BASE_MIX


def ramp_for(cfg, day):
    """The optional warm-up ramp (config `ramp`): (difficulty mix, day number, days) while it runs, else None."""
    r = cfg.get("ramp")
    if not r or not r.get("start"):
        return None
    i = (date.fromisoformat(day) - date.fromisoformat(r["start"])).days
    if i < 0 or i >= r["days"]:
        return None
    t = i / float(max(1, r["days"] - 1))
    mix = {k: r["from"][k] + t * (r["to"][k] - r["from"][k]) for k in ("easy", "medium", "hard")}
    return mix, i + 1, r["days"]


def level_label(ramp):
    if not ramp:
        return "Mixed level (as in the first set), adjusted to your recent scores"
    mix, n, days = ramp
    return "Warm-up ramp, day %d of %d: about %d%% easy, %d%% medium, %d%% hard" % (
        n, days, round(100 * mix["easy"]), round(100 * mix["medium"]), round(100 * mix["hard"]))


def spread(n, mix, rng):
    """n difficulty labels in proportion to mix, shuffled."""
    counts = {k: int(math.floor(n * p)) for k, p in mix.items()}
    order = sorted(mix, key=lambda k: -(n * mix[k] - counts[k]))
    i = 0
    while sum(counts.values()) < n:
        counts[order[i % len(order)]] += 1
        i += 1
    out = [k for k in ("easy", "medium", "hard") for _ in range(counts[k])]
    rng.shuffle(out)
    return out


def spread_carry(n, mix, carry, rng):
    """Like spread, but rounding leftovers carry over to the next call, so a whole day lands on
    its mix (each day of the ramp is then strictly harder than the one before)."""
    levels = ("easy", "medium", "hard")
    want = {k: n * mix[k] + carry.get(k, 0.0) for k in levels}
    counts = {k: max(0, int(math.floor(want[k]))) for k in levels}
    while sum(counts.values()) < n:
        counts[max(levels, key=lambda k: want[k] - counts[k])] += 1
    while sum(counts.values()) > n:
        counts[min((k for k in levels if counts[k] > 0), key=lambda k: want[k] - counts[k])] -= 1
    for k in levels:
        carry[k] = want[k] - counts[k]
    out = [k for k in levels for _ in range(counts[k])]
    rng.shuffle(out)
    return out


def last_seen(bank, section):
    """{topic: last day it was published} for one section, from the bank."""
    seen = {}
    for r in bank:
        if r.get("section") == section and r.get("topic"):
            seen[r["topic"]] = max(seen.get(r["topic"], ""), r.get("day", ""))
    return seen


def weights(topics, section, stats, seen, day):
    d0 = date.fromisoformat(day)
    out = []
    for t in topics:
        st = stats.get("topics", {}).get("%s|%s" % (section, t), {})
        att = st.get("attempted", 0)
        acc = (st.get("correct", 0) + 1.0) / (att + 2.0)  # smoothed: an unseen topic counts as 50%
        last = seen.get(t)
        gap = (d0 - date.fromisoformat(last)).days if last else 15
        boost = min(1.0 + gap / 5.0, 4.0)
        out.append((0.5 + 2.0 * (1.0 - acc)) * boost)
    return out


def weighted_sample(items, w, k, rng):
    items, w = list(items), list(w)
    out = []
    while items and len(out) < k:
        i = rng.choices(range(len(items)), weights=w)[0]
        out.append(items.pop(i))
        w.pop(i)
    return out


def blocked(cfg, section, chosen):
    """Topics in `section` that repeat an idea already chosen today (config exclusive_topics)."""
    out = set()
    for grp in cfg.get("exclusive_topics", {}).get("groups", []):
        pairs = [tuple(x) for x in grp]
        if any(p in chosen for p in pairs):
            out |= {t for s, t in pairs if s == section and (s, t) not in chosen}
    return out


def pick_topics(topics, section, stats, seen, day, k, rng, exclude=()):
    if not topics or k <= 0:
        return []
    allowed = [t for t in topics if t not in exclude]
    if len(allowed) >= min(k, len(topics)):
        topics = allowed
    w = weights(topics, section, stats, seen, day)
    out = weighted_sample(topics, w, min(k, len(topics)), rng)
    while len(out) < k:  # more slots than topics: repeat the weakest-weighted ones
        out.append(rng.choices(topics, weights=w)[0])
    return out


def make_plan(cfg, day, stats, bank, extra=True):
    rng = random.Random("aptitude-" + day)
    ph = phase_for(cfg, day)
    sections = []
    ramp = ramp_for(cfg, day)
    carry = {} if ramp else None  # day-level rounding while the ramp runs
    chosen = set()  # (section, topic) picked so far today
    for code in [c for c in SECTION_ORDER if c in ph["mix"]]:
        mix = ph["mix"][code]
        sec = cfg["sections"][code]
        n = mix["n"]
        sets = list(mix.get("sets", []))
        n_alone = n - sum(sets)
        recent = stats.get("sections", {}).get(code, {}).get("recent", [])
        dmix = ramp[0] if ramp else difficulty_mix(recent)
        seen = last_seen(bank, code)
        groups, slots = [], []
        kinds = pick_topics(sec.get("set_kinds", []), code, stats, seen, day, len(sets), rng, blocked(cfg, code, chosen))
        chosen |= {(code, k) for k in kinds}
        for i, size in enumerate(sets):
            ask = size + (1 if extra else 0)  # one spare per set, in case one fails a check
            g = {"key": "%s%d" % (code, i + 1), "kind": kinds[i] if i < len(kinds) else "set", "size": size,
                 "ask": ask, "difficulties": spread_carry(ask, dmix, carry, rng) if carry is not None else spread(ask, dmix, rng)}
            groups.append(g)
        alone_ask = n_alone + (max(1, int(math.ceil(0.2 * n_alone))) if (extra and n_alone) else 0)
        topics = pick_topics(sec.get("topics", []), code, stats, seen, day, alone_ask, rng, blocked(cfg, code, chosen))
        chosen |= {(code, t) for t in topics}
        diffs = spread_carry(alone_ask, dmix, carry, rng) if carry is not None else spread(alone_ask, dmix, rng)
        for i, t in enumerate(topics):
            slots.append({"topic": t, "difficulty": diffs[i]})
        sections.append({"code": code, "n": n, "groups": groups, "slots": slots, "difficulty_mix": dmix})
    level = {"ramp_day": ramp[1], "ramp_days": ramp[2], "mix": {k: round(v, 3) for k, v in ramp[0].items()}} if ramp else None
    return {"day": day, "phase": ph["name"], "phase_label": ph["label"], "level": level, "level_label": level_label(ramp),
            "sections": sections}
