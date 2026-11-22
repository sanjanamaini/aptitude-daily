# Aptitude Daily

Fifty new XAT and BITS BAAT practice questions every morning, each answer checked twice before you see it, and the answer key locked until you have solved the set.

Claude writes the questions. Python code recomputes every answer that can be computed, a second Claude call solves every question blind, and any question where the two disagree with the key is thrown away and written again. A small website on your own Mac serves the day's set, times you, saves your answers as you go, and opens the key with explanations only when you submit.

## A day's set

| Section | Questions | Exam |
|---|---|---|
| Verbal Ability (para jumbles, odd one out, critical reasoning, usage) | 6 | XAT, BAAT |
| Reading Comprehension (one 380-480 word argumentative passage) | 4 | XAT, BAAT |
| Decision Making (two cases of 3, plus 4 single questions) | 10 | XAT |
| Quantitative Ability | 12 | XAT, BAAT |
| Data Interpretation (one set: table, bar, line or pie chart, or caselet) | 4 | XAT, BAAT |
| Logical Reasoning (one puzzle of 3, plus 3 single questions) | 6 | BAAT, XAT |
| Analytics Aptitude (matrices, calculus, numerical methods, probability, programs, data structures, Boolean algebra) | 8 | BAAT |

That is the XAT-focused mix, used until 3 January 2027. After that the default switches to a BAAT-focused mix (more Quant, DI, Reasoning and Analytics Aptitude, no Decision Making). Both are in `config.json` under `phases`.

Sections and topics follow the published patterns: the [BITS BAAT syllabus](https://dom.bits-pilani.ac.in/admissions/baat-syllabus) (Management Aptitude and Analytics Aptitude) and the XAT pattern as reported for XAT 2027 (Verbal and Logical Ability, Decision Making, Quantitative Ability and DI; 5 options; minus 0.25 per wrong answer), both checked on 21 November 2026. XAT's General Knowledge part is left out on purpose: a language model's current-affairs knowledge goes stale.

**Topics adapt.** Topics you get wrong, and topics you have not seen for a while, come up more often. Difficulty moves up when your last three scores in a section are 80% or more and down at 45% or less. Topic pairs that test the same idea in two sections (Venn diagrams in Quant and in Reasoning, for example; the list is `exclusive_topics` in `config.json`) are never both used on one day.

**Optional warm-up ramp.** Set `ramp.start` to a date and, for `ramp.days` days, difficulty climbs in a straight line from mostly easy to mostly medium and hard, strictly harder every day, before the adaptive mix takes over.

## How every answer is checked

1. **Write.** Claude writes each section from a plan (topics, difficulty, which questions share a passage or data set), plus a spare question or two.
2. **Structure.** Exactly the right number of distinct options, a valid key, a real explanation, no LaTeX, and every question attached to a set that exists.
3. **Compute.** For anything computable (all Quant and DI, most puzzles, program output, Boolean algebra, stack and queue traces) the setter also writes Python that works the answer out from the givens. It runs in a sandbox: plain computation only, a short list of maths modules, no files, network, `eval` or double-underscore access, an isolated interpreter (`python -I`), a timeout and a CPU limit. The result must land on the keyed option, at the precision the options are printed in, and on no other option.
4. **Not a repeat.** A question whose wording overlaps an earlier one by 60% or more, with the numbers ignored, is dropped, so a question with only its numbers changed counts as a repeat.
5. **Blind solve.** A separate Claude call answers every surviving question without seeing the key. Any disagreement drops the question, and so does a flaw it reports with low confidence.
6. **Top up.** Dropped questions are written again, up to two more rounds. If a section is still short, the day ships with fewer questions and the notification says so.

The questions (`paper.json`) and the key (`key.json`) are separate files, and the website never sends the key for a day that is still locked.

**First real build (21 November 2026):** 50 of 50 questions in about 4.5 minutes and 16 Claude calls; 35 answers recomputed by code; all 50 confirmed by the blind solve; 6 drafts dropped. All 50 keys were then re-solved by hand and were correct.

**Limits, stated plainly.** Both checks for verbal, reading and decision-making questions come from Claude, so a mistake both runs share can survive; the code check covers only computable questions. If you think a key is wrong, re-solve it, and if it is, `apt.py fix` corrects it on the record and re-scores you. The questions are original practice items written by an AI, not past papers, and this project has no connection with XLRI or BITS Pilani.

## Requirements

- macOS (the daily schedule uses launchd; notifications use `osascript`)
- Python 3.9 or newer, standard library only
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code), logged in, so the `claude` command works. Each daily build makes about 16 calls on your Claude plan; the models are set in `config.json` (`models`).
- For the browser test only: `pip install playwright` and `playwright install chromium`

## Set up

```
git clone https://github.com/sanjanamaini/aptitude-daily.git
cd aptitude-daily
cp config.local.example.json config.local.json   # optional: state folder, schedule, ramp, sync folder
python3 apt.py generate     # build today's set now (5 to 15 minutes)
python3 apt.py deploy       # install the 06:30 build and the always-on website
open http://127.0.0.1:8643
```

`deploy` copies the app to `<home>/app` (default `~/.aptitude-daily/app`), because macOS does not let scheduled jobs read `~/Documents`. Run it again after any change.

## Every day

- **On the website:** the morning notification says the set is ready. Solve on the page (answers save as you go, flag questions, start the timer), then press **Submit**. The key opens with a score in XAT marking (+1, minus 0.25), each section's result, every explanation and shortcut, and how the answer was checked. **Sets** lists every day, **Progress** shows accuracy by section and topic, **Mistakes** collects everything you got wrong to try again, and **Health** shows each morning's build and every dropped draft with the reason.
- **Solved on paper:**

  ```
  python3 apt.py submit --answers "1B 2C 3D 4- 5A ..."   # or one letter per question in order; - is a skip
  python3 apt.py unlock                                  # just the key, no score
  python3 apt.py key --wrong                             # explanations for what you missed
  python3 apt.py fix --q 12 --answer C --why "..."       # a key proved wrong: corrected on the record, re-scored
  python3 apt.py status
  python3 apt.py sync --to ~/Documents/aptitude-progress # copies sets, unlocked keys and a progress.md
  ```

- **With Claude Code:** add `python3 /path/to/aptitude-daily/apt.py brief` as a `SessionStart` hook and Claude knows each session whether today's set is waiting, solved or failed. Tell it you have solved the set, with your answers, and it can run `submit` and talk you through your mistakes.

The build runs at 06:30 local time, again at 09:30, 13:00 and 18:00 if the day's set is still missing (a failed build or a Mac that was off), and at login.

## Files

| Path | What |
|---|---|
| `apt.py` | every command above, the launchd agents, the session brief |
| `plan.py` | the day's plan: phase, sets, weighted topics, difficulty, the ramp |
| `prompts.py` | the setter and blind-solver prompts, and reading their replies |
| `build.py` | write, check, blind-solve, top up, publish |
| `verify.py`, `sandbox_runner.py` | structure checks, number matching, the repeat check, the sandbox |
| `score.py` | marking, reading answers typed in chat, progress roll-ups |
| `llm.py` | the headless `claude` call (no tools, no MCP servers, no settings) |
| `server.py`, `web/` | the website: standard library, one page, no outside requests |
| `config.json` | schedule, models, exams, phases and mixes, topics, the ramp |
| `config.local.json` | your overrides (git-ignored); see `config.local.example.json` |
| `<home>/state/days/<date>/` | `paper.json`, `key.json`, `meta.json`, `draft.json`, `attempt.json` (its presence unlocks the key) |
| `<home>/state/bank.jsonl` | every published question, for the repeat check |
| `<home>/logs/` | one log per day, plus the launchd and website logs |

## Tests

```
python3 test_apt.py    # 28 tests: sandbox, number matching, planner and ramp, a full build with a fake Claude, the CLI, the website API
python test_web.py     # 32 checks in a real browser on a throwaway copy of your state, with screenshots
```

## Website safety

It listens on 127.0.0.1 only. Every request must name that host (which blocks DNS rebinding), every change needs an `X-Aptitude` header and a JSON body (which a page on another site cannot send without a preflight the server refuses), a foreign `Origin` is refused, the Content-Security-Policy allows scripts and styles from the server only, and question text is always shown as text, never as HTML.

## Licence

MIT. See `LICENSE`.
