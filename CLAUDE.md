# News Intelligence — rules for Claude Code (read every session)

Kunaal's personal pipeline: daily newspaper PDFs → articles → tags → vectors → story groups → digest + ask window. You work here on **build-and-run tasks** only. Decisions, design, codebooks, anything blind and the STATUS files live in the claude.ai Project "News Intelligence – Phase 3"; you never decide those alone.

## 1. Where the truth is
- **The claude.ai Project is the source of truth.** `_docs/` holds **read-only, dated snapshots** of its files (see `_docs/SNAPSHOT_DATE.txt`). If a snapshot looks older than the work at hand, or two documents disagree, **stop and ask Kunaal**. `02_STATUS.md` wins over every other document.
- **Git and the Mac are the truth for code and data.** Start each session with `git log --oneline -3 && git status --short` and compare with `_docs/02_STATUS.md` › Next 0.
- **Your notes go in `_work/`** (gitignored, writable): one dated running note per session (`_work/<date>_<task>.md`): what changed, commits, numbers, decisions asked, open points. Kunaal uploads it to the Project. Update it after each milestone, so nothing is lost if the session ends.

## 2. Hard rules (never bent; the settings file enforces most of them)
1. **Blind rule.** Never read, open, print, parse or summarise any `labels_*.json` (in `review/pairs/` or `review/threads/`), by any tool or script. Scoring commands that read them (`p2_dev_judgetest.py score`, `p3_dev_tracksheet.py score|agree`, `p2_dev_boundary.py`, `p2_dev_pairscore.py`) are **run by Kunaal in his own Terminal**; you may write the command for him.
2. **No paid calls (option A).** You never run anything that calls an API or costs money: `p1_archive_paper.py`, `p2_extract.py` (its referee calls Haiku), `p2_tag.py run|sample|submit|collect|status`, `p2_dev_judgetest.py haiku`, any judge / digest-writer / search-answer call, `pip install`. Give Kunaal the exact command, one line on what it does and costs, and a one-line prediction of its output; he runs it.
3. **Secrets.** Never read, print or look for API keys. The key file is outside the project and fenced off.
4. **Data.** `data/news.db` and `data/vectors.db` are opened **read-only** by Phase 3 code (`file:…?mode=ro`); Phase 3 writes only `data/intel.db`. Never edit `archive/`, `eval/`, `data/pairs/`, `data/snapshots/`, `review/tag_audit/`, hand-made sheets or keys.
5. **Git.** `git status --short` before every commit; `git add <file>` by name, never `git add .`; commit only after Kunaal approves; **Kunaal pushes**. The repo is **public**: no PDFs, article text, databases, JSON data or project docs ever go into it.
6. **Never change** `CLAUDE.md`, `.claude/`, `_docs/` or the frozen extraction code (git tag `exam-v1`: `p2_extract*.py`, `p2_screen.py`).
7. **One owner per file.** Files built in another chat (e.g. B·2.4's `p2_dev_*` tools) are not edited here unless Kunaal says so. Frozen test tools stay unchanged after their run.

## 3. How to work
- **Explain before acting.** Plain words first, then the technical term with a 2–3 line explanation of the concept behind each step. Kunaal must understand every script.
- **Before every command: one line predicting the output.** No raw output dumps: summarise numbers.
- **Test before real data:** build a small mock (synthetic `news.db` / `intel.db`) and test there first; then run on the real data; report **tested · result · not tested**.
- **Status words, exact:** RAN ON MAC · TESTED (mock) · WRITTEN, NOT RUN · PLANNED · REJECTED. Never round up.
- **Knobs:** every setting chosen without strong evidence is a named constant in one config block, with its reason and the measurement that would change it.
- **Pass lines before results:** any check that decides something gets its pass line written down before the result is seen. A failed line is answered by error analysis, not by moving the line.
- **Decisions are Kunaal's.** For any open design point, explain the options in prose (what the question is, options, examples, risks, your view and how sure you are, in words), then wait. One decision at a time.
- **Confidence in words** (fairly sure · unsure · guessing), never invented percentages.

## 4. Environment
- Run from `~/news-intel`. Two Python environments: `.venv/bin/python` (pipeline + Phase 3) and `.venv-embed/bin/python` (embeddings only). No `activate`.
- Standard library first; a new package needs Kunaal's yes (he installs it).
- The sandbox blocks network, writes outside this folder, and reads of labels and keys. If a command fails with "Operation not permitted", **do not work around it**: tell Kunaal what was blocked and why the command needed it.

## 5. Map
`_docs/03_ARCHITECTURE.md` (folders, files, daily run order) · `_docs/02_STATUS.md` (where things stand, Next) · `news_intel_inventory` in `_docs/` if present · Phase 3 design: `_docs/drafts/` (S5, S5b, S6 closeout, B·4 note, roadmap + v1 plan decisions, S7 notes).
