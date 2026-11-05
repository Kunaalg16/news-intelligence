# News Intelligence — rules for Claude Code (read every session)

Kunaal's personal pipeline: daily newspaper PDFs → articles → tags → vectors → story groups → digest + ask window. You work here on **build-and-run tasks** only. Decisions, design, codebooks and anything blind are Kunaal's; you never decide those alone. (Merged 5 Nov 2026: the 1 Nov hard rules kept word for word; sections 1 and 5 updated for the move of the docs to the Mac.) Kunaal dictates by voice: read generously and confirm your understanding in one line.

## 1. Where the truth is
- **From 5 Nov 2026 the docs live in `_docs/` on this Mac and are the source of truth** (a private git repo `Kunaalg16/news-intel-docs`). The two old claude.ai Projects are a frozen archive: `_docs/ARCHIVE_INDEX.md` says what is in them. **Name the exact file you need and ask Kunaal; never guess a path.** If two documents disagree, **stop and ask Kunaal**; the Mac copy wins over a Project copy.
- **Reading order:** `_docs/01_README.md` → `_docs/status/00_INDEX.md` → your strand's `_docs/status/<strand>.md` (K1, K2, K3 or B2.4; the brief names it). `_docs/03_ARCHITECTURE.md` only for builds. `02_STATUS.md` is frozen (1 Nov) and lives only in the archive.
- **Git and the Mac are the truth for code and data.** Start each session with `git log --oneline -3 && git status --short` and compare with `_docs/status/00_INDEX.md` › Pipeline state.
- **A code change needs a brief** in `_docs/` (or `_docs/reports/`). No brief, no code change: say so and stop.
- **Your notes go in `_work/`** (gitignored, writable): one dated running note per session (`_work/<date>_<task>.md`) and, at the end, `_work/<name>_report.md` + `_work/<name>_handoff.md`: what changed, commits, each pass line with its number, **tested · result · not tested**, decisions you made with a check that fails if each is wrong, open points; "Waiting for Kunaal" at the top if anything needs him. Update the note after each milestone. If a doc needs changing, put the proposed text in `_work/` and tell Kunaal which `_docs/` file it replaces; he applies it.

## 2. Hard rules (never bent; the settings file enforces most of them)
1. **Blind rule.** Never read, open, print, parse or summarise any `labels_*.json` (in `review/pairs/` or `review/threads/`), by any tool or script. Scoring commands that read them (`p2_dev_judgetest.py score`, `p3_dev_tracksheet.py score|agree`, `p2_dev_boundary.py`, `p2_dev_pairscore.py`) are **run by Kunaal in his own Terminal**; you may write the command for him.
2. **No paid calls (option A).** You never run anything that calls an API or costs money: `p1_archive_paper.py`, `p2_extract.py` (its referee calls Haiku), `p2_tag.py run|sample|submit|collect|status`, `p2_dev_judgetest.py haiku`, any judge / digest-writer / search-answer call, `pip install`. Give Kunaal the exact command, one line on what it does and costs, and a one-line prediction of its output; he runs it. **Kunaal types every paid call and every label scoring himself.**
3. **Secrets.** Never read, print or look for API keys; never `cat` or `source` the key file or put a key on a command line. The key file is outside the project and fenced off.
4. **Data.** `data/news.db` and `data/vectors.db` are opened **read-only** by Phase 3 code (`file:…?mode=ro`); Phase 3 writes only `data/intel.db` (and the derived `data/search.db`, `data/digests/`). Never edit `archive/`, `eval/`, `data/pairs/`, `data/snapshots/`, `review/tag_audit/`, hand-made sheets or keys. Never write a digest JSON into `_work/` (K1's app globs `_work/*.json`).
5. **Git.** `git status --short` before every commit; `git add <file>` by name, never `git add .`; commit only after Kunaal approves; **Kunaal pushes**. The repo is **public**: no PDFs, article text, databases, JSON data or project docs ever go into it (`_docs/` is its own private repo and is ignored here).
6. **Never change** `CLAUDE.md`, `.claude/`, `_docs/` or the frozen extraction code (git tag `exam-v1`: `p2_extract*.py`, `p2_screen.py`).
7. **One owner per file.** Files built in another strand or chat (e.g. B·2.4's `p2_dev_*` tools) are not edited here unless Kunaal says so. Frozen test tools stay unchanged after their run. Do not edit another strand's status file.
8. **While a test suite runs, nothing touches the app on port 8765** (no browser tab, no `curl`), or the "app.log unchanged" check fails.
9. **Model ids.** A model id is used only after its request shape is checked against the API docs plus one live call (typed by Kunaal). Sonnet 5.5 refuses forced tool calls that Haiku 5.5 accepts.

## 3. How to work
- **Explain before acting.** Plain words first, then the technical term with a 2–3 line explanation of the concept behind each step. Kunaal must understand every script.
- **Before every command: one line predicting the output.** No raw output dumps: summarise numbers.
- **Test before real data:** build a small mock (synthetic `news.db` / `intel.db`) and test there first; then run on the real data; report **tested · result · not tested**.
- **Status words, exact:** RAN ON MAC · TESTED (mock) · WRITTEN, NOT RUN · PLANNED · REJECTED. Never round up.
- **Knobs:** every setting chosen without strong evidence is a named constant in one config block, with its reason and the measurement that would change it.
- **Pass lines before results:** any check that decides something gets its pass line written down before the result is seen. A failed line is answered by error analysis, not by moving the line.
- **Decisions are Kunaal's.** For any open design point, explain the options in prose (what the question is, options, examples, risks, your view and how sure you are, in words), then wait. One decision at a time.
- **Confidence in words** (fairly sure · unsure · guessing), never invented percentages.
- **Keep sessions short** and end with the hand-off.

## 4. Environment
- Run from `~/news-intel`. Two Python environments: `.venv/bin/python` (pipeline + Phase 3) and `.venv-embed/bin/python` (embeddings only). No `activate`.
- Standard library first; a new package needs Kunaal's yes (he installs it).
- The sandbox blocks network, writes outside this folder, and reads of labels and keys. If a command fails with "Operation not permitted", **do not work around it**: tell Kunaal what was blocked and why the command needed it.

## 5. Map
`_docs/03_ARCHITECTURE.md` (folders, files, daily run order; being rebuilt, the old one is `_docs/_archive_core/`) · `_docs/status/00_INDEX.md` (where things stand) · code map of 5 Nov: `_work/code_map_2026-11-05.txt` (path, line count, first comment line of every code file; ask Kunaal to regenerate it if it looks behind) · earlier build reports and hand-offs: `_docs/reports/` · Phase 3 design: `_docs/drafts/` (S5, S5b, S6 closeout, B·4 note, roadmap, v1 plan decisions, S7 notes).

## 6. Start and end of every session (the next session, in any account, starts from files only)
Start: after the reading order in section 1, also read the newest `_work/*_handoff.md`. It may hold doc updates Kunaal has not yet copied into `_docs/`.
End:
1. Write `_work/<name>_report.md` and `_work/<name>_handoff.md`.
2. Put the full new text of your strand's status file and of your line in `status/00_INDEX.md` in `_work/proposed_docs/`, and say which `_docs/` file each replaces.
3. Add the Shipsy block (at most 10 lines) for `_docs/history/shipsy_sync_log.md`.
4. Tell Kunaal: "Time to update the project files", which files to copy into `_docs/`, and the starter line for the next session.
Nothing that matters may exist only in the conversation.
5. one build-log row for 02_PLAN.md (_docs/ is read-only here: write the row at the top of the hand-off; Kunaal pastes it).
