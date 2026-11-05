# CLAUDE.md — News Intelligence (rules for Claude Code, rewritten 5 Nov 2026)

You are working inside `~/news-intel/`, Kunaal's personal newspaper pipeline (PDFs → articles → digest/search/ask). Kunaal dictates by voice; read generously and confirm your understanding in one line.

## Read first
1. `_docs/01_README.md` → `_docs/status/00_INDEX.md` → your strand's `_docs/status/<strand>.md` (K1, K2, K3 or B2.4; the brief names it). `_docs/03_ARCHITECTURE.md` only for builds. `_docs/ARCHIVE_INDEX.md` says what exists in the old claude.ai Projects: **name the exact file you need and ask Kunaal; never guess a path.**
2. A **code change needs a brief** in `_docs/` (or `_docs/reports/`). No brief, no code change: say so and stop.

## The fence (set 1 Nov, `d5e685e`)
- You cannot write `_docs/**`, `CLAUDE.md` or `.claude/**`. You can write `_work/` (reports, hand-offs) and code/tests inside the repo. If a doc needs changing, put the proposed text in `_work/` and tell Kunaal which file it replaces; he applies it.
- Manual permission mode; strict sandbox. Do not look for ways around a denial.
- **Free and local steps only.** Never run a paid API call (Anthropic, Voyage) and never score Kunaal's labels: **Kunaal types every paid call and every label scoring himself.** Give him the exact command and a one-line prediction.
- Secrets: never `cat`, `source` or print the key file; never put a key on a command line.
- `data/` is the owner's: `news.db` and `vectors.db` are read-only; Phase 3 writes only `data/intel.db` (and derived `data/search.db`, `data/digests/`). Never write a digest JSON into `_work/` (K1's app globs `_work/*.json`).
- While a test suite runs, nothing touches the app on port 8765.

## How to report
- Every item gets exactly one status word: **RAN ON MAC · TESTED IN CHAT · WRITTEN NOT RUN · PLANNED · REJECTED.** Never round up.
- Pass lines are written **before** results. A failed line is analysed by cause; the line is not moved.
- Hand-off file in `_work/<name>_report.md` and `_name_handoff.md`: what was built, commit ids, each pass line with its number, **tested · result · not tested**, decisions you made and a check that fails if each is wrong, and "Waiting for Kunaal" at the top if anything needs him.
- Name every concept by its correct technical term with a 2–3 line mechanism. Name every setting chosen without strong evidence as a knob (one config block, reason, the measurement that would change it).
- Keep sessions short; end with the hand-off. One owner per file; do not edit another strand's files. `git status --short` before every commit; `git add <file>` by name; commit messages say what and why.

## Model and API facts
- A model id is used only after its request shape is checked against the API docs plus one live call (typed by Kunaal). Sonnet 5.5 refuses forced tool calls that Haiku 5.5 accepts.
- Python: `.venv/` for the pipeline and Phase 3; `.venv-embed/` for embeddings only.

## Where to look
`_docs/reports/` holds every earlier build report and hand-off (k1_*, k3_*). Code map of 5 Nov: `_work/code_map_2026-11-05.txt` (path, line count, first comment line of every code file). If it or `_docs/03_ARCHITECTURE.md` looks behind, ask Kunaal to regenerate it; do not guess file contents.
