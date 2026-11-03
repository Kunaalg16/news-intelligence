"""p3_daily.py — Phase 3 · the one-command morning run (Q9).
  v0.1 3 Nov 2026 (K3 B2-8): from K3 build brief 2 §B2-8 and the K3 note Q9, Q9a, Q9b.

WHAT IT DOES
  Runs the whole pipeline for today's papers in order, start to finish, after ONE
  approval:

      1 archive      file the PDFs sitting in ~/Downloads into archive/<date>/   PAID
      2 extract      PDFs in, articles out (--pipeline: referee on Business Line)  PAID
      3 tag          every untagged text, as ONE Batch API job                     PAID
                     submit → wait, checking every few minutes → collect
      4 store check  per-run counts, so a short day is visible before grouping     free
      5 embed        one vector per new text (qwen3-0.6b V1)                       free
      6 index        passages + FTS5 + passage vectors for the new texts           free
      7 group run    stage 1: today's day-groups                                   free
      8 group track  stage 2: join today's groups to earlier days                  free
      9 judge        SKIPPED — K2 has not built it yet                             —

  It stops at the first failure and names the step. It prints one summary line at the
  end. The digest is NOT in this run: it is built when the page is opened, under K1's
  daily cap, so the morning run never writes a digest nobody read.

WHY ONE COMMAND, AND WHY IT WAITS BY ITSELF
  Nine commands typed by hand in the right order, with the right interpreter and the
  right model flag, is nine chances to get it wrong before breakfast. The order is not
  arbitrary either: tags must exist before grouping (step 7 reads them), vectors before
  grouping, and passages before any question can be asked.

  Tagging goes through the Batch API (Q9b): about half price, but the answer comes back
  in minutes to hours rather than seconds. So this command SUBMITS the batch and then
  waits by itself, checking every few minutes, and carries on with steps 4-8 when the
  batch lands. One "y" at the start covers the whole run and Kunaal can walk away
  (asynchronous batch processing with polling). While it is working, `_work/daily.lock`
  exists and the page shows the last finished digest with a note instead of blocking
  (stale-while-processing).

  EVERY day waiting is extracted, not only the newest (fixed 3 Nov). The plan is
  printed before step 1 runs and lists the archived days that have no articles yet;
  once step 1 has filed new papers -- one day on a normal morning, a week of them after
  a gap -- the run recomputes that list and extracts each day, oldest first, saying so
  in one line. Following only the newest folder left 25 Oct - 2 Nov archived and unread.
  Days before `first_paper_day` are left alone; a core paper missing on a day that WAS
  extracted is named in the plan, never run silently. A `--date` given by hand extracts
  exactly that day. If a tag batch is already out, it is collected first and the new
  texts are then submitted -- resuming without submitting left new days untagged.

  If the batch has not come back within `batch_wait_hours`, the run STOPS CLEANLY rather
  than waiting forever, and says the one command that resumes it. Nothing is re-paid:
  `p2_tag.py` records every batch in `news.db`, refuses to submit while one is open, and
  skips any text that already has a record for this model and prompt version
  (idempotent), so a resumed run collects the batch that was already paid for.

DATA (one owner per file)
  writes  _work/daily.lock        while the run is going; removed on the way out, always
  reads   data/news.db (read-only, `mode=ro`)   only to see whether a batch is open
  Everything else is written by the commands it calls. This wrapper CALLS other strands'
  commands exactly as they exist and never changes them, and it never touches
  `archive/`, `eval/`, labels or keys.

HOW TO RUN (from ~/news-intel)
  .venv/bin/python src/p3_daily.py --dry-run     print the plan, the paid estimate and
                                                 the exact commands; run NOTHING
  .venv/bin/python src/p3_daily.py               the real run; asks once, then goes
  .venv/bin/python src/p3_daily.py --date 2026-11-03     a particular paper-day
  There is deliberately no flag that skips the question. The one approval is the design
  (Q9a): one `y` covers the whole run, and nothing paid starts without a person there.
  `run(assume_yes=True)` exists so the tests can drive it without stdin; it is not a
  command-line option.
  Its test (free, nothing real is run):
  .venv/bin/python tests/test_p3_daily.py

KNOBS
  One block below, each with its reason and the measurement that would change it.

SAFETY
  - Every paid step is named, with an estimate, BEFORE the question. One "y" covers the
    run; anything else runs nothing.
  - `--dry-run` runs no subprocess, writes no file and leaves no lock behind.
  - Two runs at once would pay twice for the same day, so a live lock file stops the
    second one. A lock whose process is gone (a crash, a closed lid) is taken over, and
    said out loud.
  - The lock is removed in a `finally`, so a failure or a Ctrl-C cannot leave the page
    saying "processing" for ever.
  - The daily run never calls a model itself. Everything paid happens inside the three
    commands it starts, each of which caps and logs its own spend.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "_work" / "daily.lock"
NEWS_DB = ROOT / "data" / "news.db"
VENV = ROOT / ".venv" / "bin" / "python"
VENV_EMBED = ROOT / ".venv-embed" / "bin" / "python"

# ---------------------------------------------------------------- KNOBS

KNOBS = {
    # How often the run asks whether the tag batch has come back. Five minutes is small
    # against a batch that takes minutes to hours and costs nothing to ask; a status
    # check is one cheap API call, not a tagging call.
    # Change it if: the measured batch turnaround is routinely under 5 minutes (poll
    # faster, the run finishes sooner) or routinely hours (poll slower, fewer calls).
    "batch_poll_minutes": 5,

    # When to stop waiting and hand the morning back. Six hours covers the Batch API's
    # usual turnaround with room to spare while still ending the run inside one morning;
    # past that, waiting costs nothing but tells Kunaal nothing either.
    # Change it if: the measured turnaround crosses 6 hours more than rarely, or if the
    # 24-hour Batch window is ever being hit (then the limit is the window, not this).
    "batch_wait_hours": 6,

    # `p2_tag.py` DEFAULTS to haiku-4.5, and `p3_group.py` reads tags for
    # `claude-haiku-5-5` / v1.3 only (p3_group.py:97). Tagging with the default would
    # cost real money and leave grouping with nothing to read, so the model is passed
    # explicitly here. This is a rule, not a tuned number: it must match p3_group.
    "tag_model": "haiku-5.5",

    # Passed to `p2_tag.py --cap`: the most the tag step may estimate before it refuses
    # to send. Measured daily tagging is ≈ $0.35-0.60 normal, ≈ half on Batch (design
    # §7), so $2 is several times a normal day. A day that estimates more than this is
    # not a big day, it is a re-tag of the archive or a prompt-version change — which
    # should stop and be looked at, not run at 6 a.m.
    # Change it if: a legitimate day's papers ever estimate above $2.
    "tag_cap_usd": 2.00,

    # The estimates printed before the question. Each is what it is: a measurement or a
    # guess. They buy an informed "y", nothing else — every command caps and logs its
    # own real spend, and `cost_log` is where the truth lives afterwards.
    "estimate_usd": {
        # 1-2 model calls per PDF (date, and a second front-page question when the first
        # page is a wrap ad), ~4 papers a day. Guess, from the call pattern.
        "archive": 0.02,
        # PER PAPER-DAY. MEASURED 3 Nov: the Business Line referee on one day, 16 pages,
        # $0.1330 (the old guess, $0.03, was a quarter of that).
        "extract": 0.13,
        # PER PAPER-DAY. MEASURED 3 Nov: 197 texts estimated $0.11 on Batch, ≈ $0.00056 a
        # text; a normal day is ≈ 350 texts. Was a flat $0.30 per run, which a catch-up of
        # several days made meaningless.
        "tag": 0.20,
    },

    # The archive's first paper-day. Folders before it are not extracted by this run.
    # 16 Sep is where news.db starts; archive/2026-09-15 holds one stray Economic Times
    # PDF, a title the pipeline does not otherwise read (3 Nov). Kunaal's call to change.
    "first_paper_day": "2026-09-16",

    # A lock older than this whose process is gone is treated as a crash and taken over.
    # It only has to be longer than the gap between writing the file and the first step.
    "stale_lock_minutes": 1,
}

PAID = ("archive", "extract", "tag")


# ---------------------------------------------------------------- the plan

def paper_days(root=ROOT):
    """Dated folders under `archive/`, newest first. Free, local, read-only."""
    archive = Path(root) / "archive"
    if not archive.is_dir():
        return []
    return sorted((p.name for p in archive.iterdir()
                   if p.is_dir() and len(p.name) == 10 and p.name[4] == p.name[7] == "-"),
                  reverse=True)


def _stored(root):
    """(dates with articles, extracted source stems, publishers) from news.db, read-only."""
    db = Path(root) / "data" / "news.db"
    if not db.exists():
        return set(), set(), set()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT DISTINCT date, source_file, publisher FROM placements").fetchall()
    except sqlite3.OperationalError:
        rows = []                     # no placements table yet: nothing has been extracted
    finally:
        con.close()
    return ({d for d, _f, _p in rows}, {(f or "").rsplit(".", 1)[0] for _d, f, _p in rows},
            {p for _d, _f, p in rows if p})


def unextracted_days(root=ROOT):
    """Every archived paper-day with NO articles in news.db, OLDEST first.

    Not just the newest folder: a morning that files several days at once (papers
    collected over a week) must extract all of them, or every day but the last is
    archived and silently never read. Found 3 Nov: 25 Oct - 2 Nov were filed and only
    3 Nov extracted. Grouping copes with days arriving out of order (p3_group's `track`
    re-tracks every date whose window changed), so oldest-first is for readability of
    the log, not for correctness of the links.
    """
    have, _stems, _pubs = _stored(root)
    return sorted(d for d in paper_days(root)
                  if d >= KNOBS["first_paper_day"] and d not in have)


def missing_core_papers(root=ROOT):
    """(day, pdf) for a CORE paper's PDF that was never extracted on a day that was.

    Core = a publisher news.db already holds. Other editions in the folders (Economic
    Times, The Hindu International / School) are not extracted by design, so they are
    not reported -- only a gap among the papers the pipeline does read."""
    have, stems, pubs = _stored(root)
    out = []
    for d in sorted(have):
        folder = Path(root) / "archive" / d
        if d < KNOBS["first_paper_day"] or not folder.is_dir():
            continue
        for pdf in sorted(folder.glob("*.pdf")):
            paper = pdf.stem[len(d) + 1:]
            if paper in pubs and f"{d}/{pdf.stem}" not in stems:
                out.append((d, pdf.name))
    return out


def _extract_args(day):
    return ["src/p2_extract.py", f"archive/{day}", "--pipeline"]


def plan(date=None, root=ROOT):
    """The steps, in order, with the exact command each one runs.

    `date` is the PAPER-day, not today's clock date: papers are often filed the night
    before or late in the morning, and what matters is the date printed on the paper.
    Default = the newest dated folder under `archive/`, which is what the archive step
    has just filed. Only steps 1 and 2 are date-aware; the rest work on "everything not
    done yet" by themselves, which is also what makes the run resumable.
    """
    root = Path(root)
    day = date or (paper_days(root)[0] if paper_days(root) else None)
    # Which days step 2 extracts: the one asked for, or EVERY archived day not yet
    # extracted (oldest first). Recomputed after step 1 files new papers.
    days = [date] if date else unextracted_days(root)
    tag_model = KNOBS["tag_model"]
    est = KNOBS["estimate_usd"]
    steps = [
        {"n": 1, "name": "archive", "what": "file new PDFs from ~/Downloads into archive/<date>/",
         "python": VENV, "args": ["src/p1_archive_paper.py"],
         "paid": True, "estimate": est["archive"], "kind": "command"},
        {"n": 2, "name": "extract", "what": "PDFs in, articles out (referee on Business Line)",
         "python": VENV, "args": _extract_args(days[0] if days else day),
         "paid": True, "estimate": est["extract"] * max(1, len(days)), "kind": "extract",
         "days": days, "needs_date": True},
        {"n": 3, "name": "tag", "what": f"every untagged text as ONE Batch API job ({tag_model})",
         "python": VENV, "args": ["src/p2_tag.py", "submit",
                                  "--model", tag_model,
                                  "--cap", f"{KNOBS['tag_cap_usd']:.2f}"],
         "paid": True, "estimate": est["tag"] * max(1, len(days)), "kind": "tag"},
        {"n": 4, "name": "store check", "what": "per-run counts, before anything is grouped",
         "python": VENV, "args": ["src/p2_store.py", "check"],
         "paid": False, "estimate": 0.0, "kind": "command"},
        {"n": 5, "name": "embed", "what": "one vector per new text (qwen3-0.6b V1)",
         "python": VENV_EMBED, "args": ["src/p2_embed.py", "run",
                                        "--model", "qwen3-0.6b", "--variant", "V1"],
         "paid": False, "estimate": 0.0, "kind": "command"},
        {"n": 6, "name": "index", "what": "passages, FTS5 and passage vectors for the new texts",
         "python": VENV_EMBED, "args": ["src/p3_index.py", "update"],
         "paid": False, "estimate": 0.0, "kind": "command"},
        {"n": 7, "name": "group run", "what": "stage 1: today's day-groups",
         "python": VENV, "args": ["src/p3_group.py", "run"],
         "paid": False, "estimate": 0.0, "kind": "command"},
        {"n": 8, "name": "group track", "what": "stage 2: join today's groups to earlier days",
         "python": VENV, "args": ["src/p3_group.py", "track"],
         "paid": False, "estimate": 0.0, "kind": "command"},
        {"n": 9, "name": "judge", "what": "SKIPPED — K2 has not built the judge yet",
         "python": None, "args": [], "paid": False, "estimate": 0.0, "kind": "skipped"},
    ]
    return day, steps


def shown(step, root=ROOT):
    """One step's command exactly as it would be typed, from ~/news-intel."""
    if step["kind"] == "skipped":
        return "(nothing to run)"
    if step["kind"] == "extract" and len(step.get("days") or []) != 1:
        if not step.get("days"):
            return "(no archived day is waiting to be extracted)"
        return "\n     ".join(shown(dict(step, kind="command", args=_extract_args(d)), root)
                               for d in step["days"])
    python = Path(step["python"])
    try:
        python = python.relative_to(Path(root))
    except ValueError:
        pass
    parts = [str(python)] + [str(a) for a in step["args"]]
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def print_plan(day, steps, root=ROOT):
    """The plan, the paid steps with an estimate, and the exact commands."""
    print(f"Daily run · paper-day {day or 'NONE FOUND'} · {datetime.now():%Y-%m-%d %H:%M}")
    extract = next((s for s in steps if s.get("needs_date")), {})
    waiting = extract.get("days") or []
    print(f"  waiting to be extracted now: {len(waiting)} day(s)"
          + (f" ({', '.join(waiting)})" if waiting else "")
          + " · days filed by step 1 are added after it runs")
    for d, pdf in missing_core_papers(root):
        print(f"  NOTE: {d} has articles but {pdf} was never extracted (a core paper) "
              f"-- not run automatically; run p2_extract.py on it if it should be")
    print()
    for s in steps:
        money = f"PAID ≈ ${s['estimate']:.2f}" if s["paid"] else ("—" if s["kind"] == "skipped" else "free")
        print(f"  {s['n']}. {s['name']:<12} {money:<14} {s['what']}")
        print(f"     {shown(s, root)}")
    print()
    total = sum(s["estimate"] for s in steps if s["paid"])
    paid_names = ", ".join(s["name"] for s in steps if s["paid"])
    print(f"Paid steps: {paid_names} · estimated total ≈ ${total:.2f} "
          f"(estimates, not quotes; each command caps and logs its own spend)")
    print(f"Tagging goes through the Batch API: submit, then check every "
          f"{KNOBS['batch_poll_minutes']} min for up to {KNOBS['batch_wait_hours']} h, then collect.")
    return total


# ---------------------------------------------------------------- the lock

def read_lock():
    """What the lock file says, or None when there is none."""
    if not LOCK.exists():
        return None
    try:
        return json.loads(LOCK.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}          # a lock we cannot read is still a lock


def alive(pid):
    """Is that process still there? Signal 0 asks without sending anything."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def take_lock(day):
    """Write the lock, or refuse because a run is already going.

    Returns (True, "") when the lock is ours. Two runs at once would pay twice for the
    same papers, so a LIVE lock stops the second one; a lock whose process has gone (a
    crash, a closed lid) is taken over and said out loud, because the alternative is a
    page stuck on "today's papers still processing" until someone deletes a file.
    """
    held = read_lock()
    if held is not None:
        if alive(held.get("pid")):
            return False, (f"a daily run is already going (pid {held.get('pid')}, started "
                           f"{held.get('started_at')}, on step {held.get('step')}). "
                           f"Two runs at once would pay twice for the same papers.")
        print(f"note: taking over a lock left behind by pid {held.get('pid')} "
              f"(started {held.get('started_at')}, no such process now)")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    write_lock(day, "starting")
    return True, ""


def write_lock(day, step):
    """Keep the lock current, so a look at it says what the run is doing."""
    LOCK.write_text(json.dumps({
        "pid": os.getpid(),
        "day": day,
        "step": step,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }), encoding="utf-8")


def drop_lock():
    LOCK.unlink(missing_ok=True)


# ---------------------------------------------------------------- running

def run_command(step, runner, root=ROOT):
    """Start one step and hand back its exit code."""
    return runner([str(step["python"])] + [str(a) for a in step["args"]], root)


def subprocess_runner(cmd, root=ROOT):
    """The real runner: the command's own output goes straight to the screen."""
    return subprocess.run(cmd, cwd=str(root)).returncode


def tee_runner(cmd, root=ROOT, keep=None):
    """Like `subprocess_runner`, and every line ALSO appended to `keep`.

    For extract: `p2_extract.py` prints each paper's quality line -- coverage %, flagged
    pages, jumps joined, ads, pages screened -- and stores it nowhere, and the extractor
    is frozen (exam-v1). Kept here, per paper-day, those numbers survive the terminal
    (found 3 Nov: every day before it is lost). The screen still shows each line live.
    """
    keep = Path(keep) if keep else None
    if keep:
        keep.parent.mkdir(parents=True, exist_ok=True)
    out = keep.open("a", encoding="utf-8") if keep else None
    try:
        if out:
            out.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S} {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if out:
                out.write(line)
        return proc.wait()
    finally:
        if out:
            out.close()


def extract_log(day, root=ROOT):
    """Where one paper-day's extract output is kept: data/logs/extract_<day>.txt."""
    return Path(root) / "data" / "logs" / f"extract_{day}.txt"


def open_batch(root=ROOT):
    """The id of a tag batch already out, read from news.db read-only; None if none.

    Asked before submitting, so a resumed run collects the batch that was already paid
    for instead of looking like a failure. `p2_tag.py submit` refuses while a batch is
    open anyway — this only makes the resume quiet and deliberate rather than an error.
    """
    db = Path(root) / "data" / "news.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("SELECT batch_id FROM batches WHERE status != 'collected' "
                          "ORDER BY submitted_at LIMIT 1").fetchone()
        con.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None        # no `batches` table yet = nothing is out


def batch_finished(output):
    """Has the batch come back? Read from `p2_tag.py status` output.

    `status` prints one line per batch: "<id>: ended · processing 0, succeeded N, ..."
    or "<id>: collected (...)". A batch still working says `in_progress` (or
    `canceling`). Finished = no line is still working, and at least one line was there:
    output we cannot read is treated as NOT finished, so an unreadable status waits for
    the limit instead of sending the run on to collect nothing.
    """
    lines = [ln for ln in (output or "").splitlines() if ":" in ln]
    if not lines:
        return False
    working = any(("in_progress" in ln) or ("canceling" in ln) for ln in lines)
    done = any((": ended" in ln) or (": collected" in ln) for ln in lines)
    return done and not working


def run_tag(step, runner, capture, sleep, root=ROOT, log=print):
    """Step 3: submit (unless a batch is already out), wait, collect.

    Returns "ok", "paused" (the wait limit came first — nothing lost, nothing re-paid)
    or "failed".
    """
    model = KNOBS["tag_model"]
    cap = f"{KNOBS['tag_cap_usd']:.2f}"
    already = open_batch(root)
    if already:
        # Collect what is already out FIRST, then still submit: the texts extracted in
        # THIS run are not in that batch. Resuming without submitting left a catch-up's
        # days extracted and never tagged (found 3 Nov, with 3 Nov's batch still open).
        # p2_tag skips texts that already have a record, so nothing is paid twice, and
        # it says "Nothing to tag" and exits 0 when there is nothing new.
        log(f"  a batch is already out ({already}); collecting it first, nothing re-paid")
        first = _wait_collect(step, runner, capture, sleep, root, log)
        if first != "ok":
            return first
        log("  now submitting whatever is still untagged (this run's new texts)")
    code = run_command(step, runner, root)
    if code != 0:
        return "failed"
    return _wait_collect(step, runner, capture, sleep, root, log)


def _wait_collect(step, runner, capture, sleep, root=ROOT, log=print):
    """Poll `status` until the batch is back, then `collect`. "ok" | "paused" | "failed"."""
    model = KNOBS["tag_model"]
    poll = [str(VENV), "src/p2_tag.py", "status", "--model", model]
    collect = [str(VENV), "src/p2_tag.py", "collect", "--model", model]
    waited, limit = 0.0, KNOBS["batch_wait_hours"] * 3600
    gap = KNOBS["batch_poll_minutes"] * 60
    while True:
        code, out = capture(poll, root)
        if code != 0:
            return "failed"
        if batch_finished(out):
            log(f"  batch is back after {waited / 60:.0f} min; collecting")
            return "ok" if runner(collect, root) == 0 else "failed"
        if waited >= limit:
            log(f"  batch still out after {KNOBS['batch_wait_hours']} h; stopping cleanly. "
                f"Nothing is lost and nothing is re-paid — resume with:")
            log(f"      .venv/bin/python src/p3_daily.py --date {step.get('day', '')}".rstrip())
            return "paused"
        log(f"  batch still out ({waited / 60:.0f} min); checking again in "
            f"{KNOBS['batch_poll_minutes']} min")
        sleep(gap)
        waited += gap


def capture_runner(cmd, root=ROOT):
    """Run a command and give back (code, output) — for the status poll only."""
    p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    if p.stdout:
        sys.stdout.write(p.stdout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run(date=None, dry_run=False, assume_yes=False, root=ROOT,
        runner=None, capture=None, sleep=None, ask=None, log=print):
    """The whole morning. Everything the test replaces is a parameter with a default."""
    runner = runner or subprocess_runner
    capture = capture or capture_runner
    sleep = sleep or time.sleep
    ask = ask or (lambda: input("Run it? [y/N] "))

    day, steps = plan(date, root)
    total = print_plan(day, steps, root)

    if dry_run:
        log("\n--dry-run: nothing was run, nothing was written.")
        return 0
    if day is None:
        log("\nNo dated folder under archive/ and no --date: nothing to extract. "
            "Stopped before step 1.")
        return 1
    if not assume_yes:
        answer = (ask() or "").strip().lower()
        if answer != "y":
            log("Nothing run.")
            return 1

    ours, why = take_lock(day)
    if not ours:
        log(f"\nSTOPPED before step 1: {why}")
        return 1

    started = time.time()
    ran, paused = [], None
    try:
        for step in steps:
            write_lock(day, f"{step['n']} {step['name']}")
            if step["kind"] == "skipped":
                log(f"\n[{step['n']}/{len(steps)}] {step['name']}: {step['what']}")
                continue
            log(f"\n[{step['n']}/{len(steps)}] {step['name']} · {shown(step, root)}")
            if step["name"] == "archive":
                if run_command(step, runner, root) != 0:
                    return stop(step, started, ran, log)
                ran.append(step["name"])
                # The archive step has just filed new PDFs -- possibly SEVERAL days of
                # them (a week of papers collected at once). The plan was drawn before
                # step 1, so recompute EVERY archived day not yet extracted, oldest
                # first, and say so. Following only the newest folder is what left
                # 25 Oct - 2 Nov archived and unread on 3 Nov.
                if date is None:
                    waiting = unextracted_days(root)
                    for s in steps:
                        if s.get("needs_date"):
                            s["days"] = waiting
                            s["args"] = _extract_args(waiting[0]) if waiting else s["args"]
                    if waiting:
                        day = waiting[-1]
                        log(f"  papers filed: {len(waiting)} day(s) to extract, oldest first: "
                            f"{', '.join(waiting)}")
                    else:
                        log("  no archived day is waiting to be extracted")
                continue
            if step["kind"] == "extract":
                for d in step.get("days") or []:
                    one = dict(step, kind="command", args=_extract_args(d))
                    log(f"  extract {d} · {shown(one, root)}")
                    code = (tee_runner([str(one["python"])] + [str(a) for a in one["args"]],
                                       root, keep=extract_log(d, root))
                            if runner is subprocess_runner else run_command(one, runner, root))
                    if code != 0:
                        return stop(dict(step, what=f"{step['what']} · failed on {d}"),
                                    started, ran, log)
                ran.append(step["name"])
                continue
            if step["kind"] == "tag":
                outcome = run_tag(dict(step, day=day), runner, capture, sleep, root, log)
                if outcome == "failed":
                    return stop(step, started, ran, log)
                if outcome == "paused":
                    paused = step
                    break
            elif run_command(step, runner, root) != 0:
                return stop(step, started, ran, log)
            ran.append(step["name"])
    finally:
        drop_lock()

    mins = (time.time() - started) / 60
    if paused:
        log(f"\nDaily run PAUSED at step {paused['n']} {paused['name']} · "
            f"{len(ran)} of {len(steps)} steps done · {mins:.0f} min · "
            f"the tag batch is out and paid for; re-run to collect it and finish.")
        return 2
    log(f"\nDaily run finished · paper-day {day} · {len(ran)} steps ran, "
        f"1 skipped (judge) · {mins:.0f} min · paid steps ran: "
        f"{', '.join(n for n in ran if n in PAID) or 'none'} · estimated ≈ ${total:.2f} "
        f"(real spend: cost_log and each command's own log)")
    return 0


def stop(step, started, ran, log=print):
    """One failure, named, with the steps that did run and what to do next."""
    mins = (time.time() - started) / 60
    log(f"\nDaily run STOPPED at step {step['n']} · {step['name']} — "
        f"{step['what']}\n"
        f"  {len(ran)} step(s) finished before it ({', '.join(ran) or 'none'}) · "
        f"{mins:.0f} min\n"
        f"  Fix it, then run the same command again: the steps that finished are not "
        f"redone (each one works on what is not done yet) and nothing is paid twice.")
    return 1


def main():
    p = argparse.ArgumentParser(description="the one-command morning run (K3 Q9)")
    p.add_argument("--date", help="paper-day YYYY-MM-DD (default: newest folder in archive/)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the steps, the estimate and the commands; run nothing")
    # No --yes on purpose: see HOW TO RUN. The approval is the design, not a convenience.
    args = p.parse_args()
    try:
        return run(date=args.date, dry_run=args.dry_run)
    except KeyboardInterrupt:
        drop_lock()
        print("\nStopped by Ctrl-C. The lock is gone; re-run to carry on.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
