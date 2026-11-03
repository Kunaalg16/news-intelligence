"""
test_p3_daily.py -- TEST (free, no API key, no internet, no subprocess). Checks src/p3_daily.py.

WHAT IT DOES
    Runs the daily wrapper with every step STUBBED: a recording runner stands in for
    `subprocess.run`, a canned string stands in for `p2_tag.py status`, and `sleep` does
    nothing. So the order, the commands, the approval, the failure behaviour and the lock
    can all be checked without filing a PDF, calling a model or spending a cent. The real
    run is NEVER started by this test (every third step of it costs money).
    It also redirects the lock to a temporary file, so the real `_work/daily.lock` -- the
    file the page reads to say "today's papers still processing" -- is never touched.

    Five parts:
      1. the plan    : nine steps in order, the right interpreter for each, and the two
                       flags that would silently break the day if they went missing
      2. dry run     : prints the steps, the estimate and the exact commands, and runs
                       NOTHING -- no subprocess, no lock, no file
      3. the lock    : it exists at every step while the run is going, is gone when the
                       run ends, is gone after a FAILURE too, a dead lock is taken over,
                       and a live one stops a second run without being deleted
      4. failure     : an injected failure at STEP 4 stops the run there, names step 4,
                       and never starts steps 5-9
      4b. the day    : the plan is drawn before step 1 files today's PDFs, so extract
                       has to follow the day step 1 actually filed -- and must not
                       override a --date given by hand
      5. the batch   : submit -> wait -> collect; a batch already out is resumed and not
                       re-submitted; a batch that never comes back pauses cleanly

WHY IT EXISTS
    This wrapper's whole job is to be trusted with one "y". Three of its nine steps cost
    money, and two of its failure modes are expensive rather than noisy: paying twice for
    the same papers (two runs at once, or a re-submitted batch), and a lock left behind
    after a crash, which leaves the page saying "processing" for ever. Those are what
    parts 3 and 5 are about.

PASS LINES (fixed before the run, K3 brief 2 §B2-8)
    (1) `--dry-run` prints the steps in order, the paid estimate and the exact commands,
        and runs nothing -- no subprocess, no file written, no lock file left behind.
    (2) A mock run with every step stubbed stops at an INJECTED failure at step 4 and
        names that step.
    (3) The lock file appears while the run is going and is gone afterwards, including
        after a failure.
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_daily.py. Takes about a second.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_daily.py
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import p3_daily as P                                                   # noqa: E402

DAY = "2026-11-03"
CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def project(tmp, with_open_batch=False):
    """A temporary project root: one archive day, and optionally a tag batch still out."""
    root = Path(tmp)
    (root / "archive" / DAY).mkdir(parents=True, exist_ok=True)
    (root / "archive" / "2026-11-01").mkdir(parents=True, exist_ok=True)
    (root / "_work").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    db = root / "data" / "news.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE batches (batch_id TEXT, model TEXT, prompt_version TEXT, "
                "submitted_at TEXT, requests INTEGER, status TEXT, collected_at TEXT)")
    # A normal morning: yesterday's papers are extracted, today's folder is waiting.
    con.execute("CREATE TABLE placements (date TEXT, publisher TEXT, source_file TEXT)")
    con.execute("INSERT INTO placements VALUES ('2026-11-01', 'Mint', "
                "'2026-11-01/2026-11-01_Mint.json')")
    if with_open_batch:
        con.execute("INSERT INTO batches VALUES "
                    "('batch_abc','haiku-5.5','v1.3','2026-11-03T05:00:00',312,'submitted',NULL)")
    con.commit()
    con.close()
    return root


class Stub:
    """Stands in for every command the wrapper starts, and records what it was asked.

    `fail_on` is matched against the whole command line, so a test can break exactly one
    step. `lock_seen` records whether the lock file existed at each call -- that is how
    pass line (3) is checked from the inside, while the run is going, rather than
    guessed at afterwards.
    """

    def __init__(self, fail_on=None, status="batch_abc: ended · processing 0, succeeded 312"):
        self.fail_on, self.status = fail_on, status
        self.calls, self.lock_seen, self.slept = [], [], []

    def runner(self, cmd, root=None):
        line = " ".join(cmd)
        self.calls.append(line)
        self.lock_seen.append(P.LOCK.exists())
        return 1 if (self.fail_on and self.fail_on in line) else 0

    def capture(self, cmd, root=None):
        line = " ".join(cmd)
        self.calls.append(line)
        self.lock_seen.append(P.LOCK.exists())
        return 0, self.status

    def sleep(self, seconds):
        self.slept.append(seconds)

    def ran(self, needle):
        return [c for c in self.calls if needle in c]


def go(root, stub, **kw):
    """Run the wrapper against the stub and give back (exit code, everything printed)."""
    out = io.StringIO()
    kw.setdefault("assume_yes", True)
    with contextlib.redirect_stdout(out):
        code = P.run(root=root, runner=stub.runner, capture=stub.capture,
                     sleep=stub.sleep, **kw)
    return code, out.getvalue()


# ---------------------------------------------------------------- 1. the plan

def the_plan(root):
    day, steps = P.plan(root=root)
    check("the paper-day is the newest folder in archive/", day, DAY)
    check("nine steps, in the designed order",
          [s["name"] for s in steps],
          ["archive", "extract", "tag", "store check", "embed", "index",
           "group run", "group track", "judge"])
    check("step numbers are 1..9", [s["n"] for s in steps], list(range(1, 10)))
    check("three paid steps, and they are the three that call a model",
          [s["name"] for s in steps if s["paid"]], ["archive", "extract", "tag"])
    check("the judge step exists and is skipped",
          (steps[8]["kind"], "SKIPPED" in steps[8]["what"]), ("skipped", True))

    # The embedding steps run in the OTHER environment. `.venv` has no
    # sentence-transformers and `.venv-embed` has no fastapi (facts §6), so a step with
    # the wrong interpreter fails on an import, every morning.
    env = {s["name"]: Path(s["python"]).parent.parent.name for s in steps if s["python"]}
    check("embed runs in .venv-embed", env["embed"], ".venv-embed")
    check("index runs in .venv-embed", env["index"], ".venv-embed")
    check("the pipeline steps run in .venv",
          [env[n] for n in ("archive", "extract", "tag", "store check", "group run", "group track")],
          [".venv"] * 6)

    cmds = {s["name"]: P.shown(s, root) for s in steps}
    # The one that would cost money and leave grouping with nothing: p2_tag.py defaults
    # to haiku-4.5, p3_group.py reads claude-haiku-5-5 / v1.3 only (p3_group.py:97).
    check("the tag step names the model p3_group actually reads",
          "--model haiku-5.5" in cmds["tag"], True)
    check("the tag step submits (the Batch API path), not `run`",
          ("p2_tag.py submit" in cmds["tag"], "p2_tag.py run" in cmds["tag"]), (True, False))
    check("the tag step carries a cap", "--cap 2.00" in cmds["tag"], True)
    # --pipeline is "THE DAILY SETUP" in p2_extract.py's own usage block.
    check("extract uses the daily setup and the paper-day",
          f"src/p2_extract.py archive/{DAY} --pipeline" in cmds["extract"], True)
    check("embed asks for the vectors grouping reads (qwen3-0.6b V1)",
          "--model qwen3-0.6b --variant V1" in cmds["embed"], True)
    check("a named --date overrides the newest folder",
          P.plan("2026-11-01", root=root)[0], "2026-11-01")


# ---------------------------------------------------------------- 2. dry run

def dry(root):
    stub = Stub()
    before = sorted(p.name for p in (root / "_work").iterdir())
    code, out = go(root, stub, dry_run=True)

    check("dry run exits 0", code, 0)
    check("dry run starts nothing at all", stub.calls, [])
    check("dry run leaves no lock behind", P.LOCK.exists(), False)
    check("dry run writes no file", sorted(p.name for p in (root / "_work").iterdir()), before)
    check("dry run says it ran nothing", "nothing was run" in out, True)

    check("dry run prints all nine steps",
          all(n in out for n in ("archive", "extract", "tag", "store check", "embed",
                                 "index", "group run", "group track", "judge")), True)
    check("dry run prints the steps in order",
          out.index("1. archive") < out.index("3. tag") < out.index("9. judge"), True)
    check("dry run prints the paid estimate per step and the total",
          ("PAID ≈ $0.20" in out, "estimated total ≈ $0.35" in out), (True, True))
    check("dry run names which steps are paid",
          "Paid steps: archive, extract, tag" in out, True)
    check("dry run prints the exact commands",
          (f"src/p2_extract.py archive/{DAY} --pipeline" in out,
           "src/p2_tag.py submit --model haiku-5.5" in out,
           "src/p3_group.py track" in out), (True, True, True))
    check("dry run says how the batch wait works",
          ("every 5 min" in out, "up to 6 h" in out), (True, True))


# ---------------------------------------------------------------- 3. the lock

def lock(root):
    # (a) a clean run: the lock is there at every step, and gone at the end
    stub = Stub()
    code, out = go(root, stub)
    check("a clean mock run exits 0", code, 0)
    check("the lock existed at every step while the run was going",
          set(stub.lock_seen), {True})
    check("the lock is gone when the run ends", P.LOCK.exists(), False)
    check("the summary line is printed once",
          out.count("Daily run finished"), 1)
    check("the summary names the paid steps that ran",
          "paid steps ran: archive, extract, tag" in out, True)
    check("the summary says the judge was skipped", "1 skipped (judge)" in out, True)

    # (b) the lock is gone after a FAILURE too. A lock left behind by a crashed run
    # leaves the page saying "today's papers still processing" until someone finds the
    # file -- the one failure of this wrapper that is silent rather than loud.
    stub = Stub(fail_on="p2_store.py check")
    code, out = go(root, stub)
    check("the lock is gone after a failure", P.LOCK.exists(), False)
    check("a failed run exits 1", code, 1)

    # (c) a lock whose process is dead is taken over, not obeyed
    P.LOCK.write_text(json.dumps({"pid": 999999999, "day": DAY, "step": "4 store check",
                                  "started_at": "2026-11-03T05:00:00"}), encoding="utf-8")
    stub = Stub()
    code, out = go(root, stub)
    check("a dead lock is taken over", code, 0)
    check("and taking it over is said out loud", "taking over a lock" in out, True)
    check("the taken-over lock is gone at the end", P.LOCK.exists(), False)

    # (d) a LIVE lock stops a second run -- two runs would pay twice for the same papers
    # -- and the other run's lock is left alone
    P.LOCK.write_text(json.dumps({"pid": os.getpid(), "day": DAY, "step": "3 tag",
                                  "started_at": "2026-11-03T05:00:00"}), encoding="utf-8")
    stub = Stub()
    code, out = go(root, stub)
    check("a live lock stops a second run", code, 1)
    check("the second run starts nothing", stub.calls, [])
    check("it says why, in money terms", "pay twice" in out, True)
    check("and it does NOT delete the other run's lock", P.LOCK.exists(), True)
    P.LOCK.unlink()

    # (e) the approval: one "y" covers the run, anything else runs nothing
    stub = Stub()
    code, out = go(root, stub, assume_yes=False, ask=lambda: "n")
    check("an answer that is not y runs nothing", (code, stub.calls), (1, []))
    check("and leaves no lock", P.LOCK.exists(), False)
    check("the paid steps were listed BEFORE the question",
          out.index("estimated total") < out.index("Nothing run"), True)
    stub = Stub()
    code, _ = go(root, stub, assume_yes=False, ask=lambda: "y\n")
    check("one y covers the whole run", (code, len(stub.ran("src/"))), (0, 10))


# ---------------------------------------------------------------- 4. failure at step 4

def failure(root):
    stub = Stub(fail_on="p2_store.py check")
    code, out = go(root, stub)

    check("an injected failure at step 4 stops the run", code, 1)
    check("and names step 4 by number and name",
          ("STOPPED at step 4" in out, "store check" in out), (True, True))
    check("the three steps before it did run",
          [c.split("src/")[1].split()[0] for c in stub.ran("src/")][:3],
          ["p1_archive_paper.py", "p2_extract.py", "p2_tag.py"])
    check("the steps after it never started",
          (stub.ran("p2_embed.py"), stub.ran("p3_index.py"),
           stub.ran("p3_group.py")), ([], [], []))
    check("it says what finished before the failure",
          "archive, extract, tag" in out, True)
    check("it says a re-run does not pay twice", "paid twice" in out, True)

    # a failure in the FIRST step stops just as cleanly
    stub = Stub(fail_on="p1_archive_paper.py")
    code, out = go(root, stub)
    check("a failure at step 1 stops at step 1", ("STOPPED at step 1" in out, code), (True, 1))
    check("and nothing after it runs", len(stub.ran("src/")), 1)


# ------------------------------- 4a. a catch-up: several days filed at once (3 Nov bug)

def catch_up(tmp):
    """Found 3 Nov on the real run: step 1 filed 28 Oct - 3 Nov, extract followed only
    the newest folder, and six days were archived but never read. Every day without
    articles must be extracted, oldest first; a day BEFORE the archive's first day is not;
    a core paper missing on an extracted day is named, never run silently."""
    root = project(Path(tmp) / "catchup")
    P.LOCK = root / "_work" / "daily.lock"
    for d in ("2026-10-28", "2026-10-30", "2026-09-15"):
        (root / "archive" / d).mkdir(parents=True, exist_ok=True)
    (root / "archive" / "2026-11-01" / "2026-11-01_The Hindu.pdf").write_bytes(b"%PDF")
    (root / "archive" / "2026-11-01" / "2026-11-01_Mint_International.pdf").write_bytes(b"%PDF")
    con = sqlite3.connect(root / "data" / "news.db")
    con.execute("INSERT INTO placements VALUES ('2026-11-01', 'The Hindu', "
                "'2026-11-01/2026-11-01_Other.json')")
    con.commit()
    con.close()
    stub = FilingStub(root, "2026-11-04")
    code, out = go(root, stub)
    extracted = [c.split("archive/")[1].split()[0] for c in stub.ran("p2_extract.py")]
    check("[catch-up] every unextracted day is extracted, oldest first",
          extracted, ["2026-10-28", "2026-10-30", DAY, "2026-11-04"])
    check("[catch-up] a folder before first_paper_day (15 Sep) is not extracted",
          "2026-09-15" in " ".join(extracted), False)
    check("[catch-up] an already-extracted day is not extracted again",
          "2026-11-01" in extracted, False)
    check("[catch-up] the plan says how many days are waiting before anything runs",
          "waiting to be extracted now: 3 day(s)" in out.split("[1/9]")[0], True)
    check("[catch-up] a CORE paper never extracted on an extracted day is named",
          "2026-11-01_The Hindu.pdf was never extracted" in out, True)
    check("[catch-up] a non-core edition (International) is not reported",
          "Mint_International" in out, False)
    check("[catch-up] the run finishes", code, 0)
    check("[catch-up] the final line names the days extracted, not one paper-day",
          "extracted 4 days, 2026-10-28 – 2026-11-04" in out, True)
    stub = FilingStub(root, "2026-11-05", fail_on="archive/2026-10-30")
    code, out = go(root, stub)
    check("[catch-up] a failing day stops the run and is named",
          (code, "failed on 2026-10-30" in out), (1, True))


# ------------------------- 4a2. extract's quality lines are kept, not only printed

def tee_check(tmp):
    """p2_extract prints each paper's coverage / flags / jumps / ads and stores them
    nowhere; the wrapper keeps a copy per day. Checked on a tiny real command."""
    root = Path(tmp) / "tee"
    root.mkdir()
    log = P.extract_log("2026-11-05", root)
    code = P.tee_runner([sys.executable, "-c", "print('X.pdf  92 articles | coverage 100.0%')"],
                        root, keep=log)
    kept = log.read_text() if log.exists() else ""
    check("[tee] the command's exit code comes back", code, 0)
    check("[tee] its quality line is kept in data/logs/extract_<day>.txt",
          "coverage 100.0%" in kept, True)
    check("[tee] with a header naming the command that wrote it", kept.startswith("# "), True)
    P.tee_runner([sys.executable, "-c", "print('second')"], root, keep=log)
    check("[tee] a re-run appends, never overwrites", "coverage 100.0%" in log.read_text(), True)


# --------------------------------------- 4b. the day the archive step has just filed

class FilingStub(Stub):
    """Like Stub, but the archive step actually files a paper, as the real one does."""

    def __init__(self, root, new_day, **kw):
        super().__init__(**kw)
        self.root, self.new_day = Path(root), new_day

    def runner(self, cmd, root=None):
        code = super().runner(cmd, root)
        if "p1_archive_paper.py" in " ".join(cmd):
            (self.root / "archive" / self.new_day).mkdir(parents=True, exist_ok=True)
        return code


def after_filing(tmp):
    """The plan is drawn BEFORE step 1 files today's papers, so it names yesterday.

    This is the normal morning: the PDFs arrive on WhatsApp, the run is started, step 1
    files them, and only then does today's folder exist. Extract has to follow the
    papers that were just filed -- otherwise the one-command run quietly re-extracts
    yesterday, every day, and today's paper is never read.
    """
    root = project(Path(tmp) / "filing")
    P.LOCK = root / "_work" / "daily.lock"
    stub = FilingStub(root, "2026-11-04")
    code, out = go(root, stub)

    check("the plan was drawn with the newest folder that existed then",
          f"archive/{DAY} --pipeline" in out.split("[1/9]")[0], True)
    extracted = [c.split("archive/")[1].split()[0] for c in stub.ran("p2_extract.py")]
    check("extract ran on EVERY waiting day, the filed one included, oldest first",
          extracted, [DAY, "2026-11-04"])
    check("and the catch-up is said out loud, with the days",
          ("2 day(s) to extract, oldest first" in out, f"{DAY}, 2026-11-04" in out),
          (True, True))
    check("the run still finishes", code, 0)

    # a named --date is Kunaal's instruction and is never overridden
    root = project(Path(tmp) / "filing_fixed")
    P.LOCK = root / "_work" / "daily.lock"
    stub = FilingStub(root, "2026-11-04")
    code, out = go(root, stub, date="2026-11-01")
    check("a named --date is not overridden by what step 1 files",
          "src/p2_extract.py archive/2026-11-01 --pipeline" in stub.ran("p2_extract.py")[0],
          True)
    check("and nothing is said about changing the day",
          "papers just filed" in out, False)


# ---------------------------------------------------------------- 5. the batch

def batch(tmp):
    # (a) the normal path: submit, one status check that says it is back, then collect
    root = project(Path(tmp) / "normal")
    P.LOCK = root / "_work" / "daily.lock"
    stub = Stub()
    code, out = go(root, stub)
    check("the batch is submitted", len(stub.ran("p2_tag.py submit")), 1)
    check("then its status is checked", len(stub.ran("p2_tag.py status")), 1)
    check("then it is collected", len(stub.ran("p2_tag.py collect")), 1)
    check("submit comes before collect",
          stub.calls.index([c for c in stub.calls if "submit" in c][0]) <
          stub.calls.index([c for c in stub.calls if "collect" in c][0]), True)
    check("collect uses the same model as submit",
          "--model haiku-5.5" in stub.ran("p2_tag.py collect")[0], True)
    check("a batch that is back is not waited for", stub.slept, [])

    # (b) a batch already out: COLLECTED FIRST, then one submit for this run's new texts.
    # Submitting first would be refused by p2_tag; not submitting at all left a catch-up's
    # days extracted and untagged (3 Nov). p2_tag skips tagged texts, so nothing re-pays.
    root = project(Path(tmp) / "resume", with_open_batch=True)
    P.LOCK = root / "_work" / "daily.lock"
    stub = Stub()
    code, out = go(root, stub)
    tag_calls = [c.split("p2_tag.py ")[1].split()[0] for c in stub.ran("p2_tag.py")]
    check("a batch already out is collected BEFORE any submit",
          tag_calls.index("collect") < tag_calls.index("submit"), True)
    check("then exactly one submit runs, for the texts this run extracted",
          tag_calls.count("submit"), 1)
    check("and the resume says nothing was re-paid", "nothing re-paid" in out, True)
    check("the rest of the run still happens", code, 0)
    check("open_batch reads the id from news.db read-only", P.open_batch(root), "batch_abc")

    # (c) a batch that never comes back: stop cleanly at the wait limit. Nothing is lost
    # and nothing is re-paid -- the batch is already paid for and `collect` can fetch it
    # later.
    root = project(Path(tmp) / "slow")
    P.LOCK = root / "_work" / "daily.lock"
    stub = Stub(status="batch_abc: in_progress · processing 312, succeeded 0")
    code, out = go(root, stub)
    check("a batch that never comes back pauses, not fails", code, 2)
    check("it is never collected", stub.ran("p2_tag.py collect"), [])
    check("it waited the whole limit, in 5-minute steps",
          (len(stub.slept), set(stub.slept)), (72, {300}))
    check("6 h of 5-minute waits is 72 checks", 72 * 5 / 60, 6.0)
    check("it says how to resume", "resume with" in out, True)
    check("and the resume is the plain command -- a --date would re-extract (and re-pay) "
          "a day that is done", "p3_daily.py --date" in out.split("resume with")[1], False)
    check("it says the batch is paid for", "out and paid for" in out, True)
    check("the steps after tagging did not run", stub.ran("p2_embed.py"), [])
    check("and the lock is gone even after a pause", P.LOCK.exists(), False)

    # (d) reading `status` output: anything we cannot read means NOT finished, so an
    # unreadable status waits for the limit instead of collecting nothing
    check("ended = finished", P.batch_finished("b1: ended · processing 0, succeeded 3"), True)
    check("collected = finished", P.batch_finished("b1: collected (312 requests, submitted x)"), True)
    check("in_progress = not finished",
          P.batch_finished("b1: in_progress · processing 312, succeeded 0"), False)
    check("one ended and one still working = not finished",
          P.batch_finished("b1: collected (1)\nb2: in_progress · processing 2"), False)
    check("'No batches yet.' = not finished", P.batch_finished("No batches yet."), False)
    check("empty output = not finished", P.batch_finished(""), False)
    check("output we cannot read = not finished", P.batch_finished("something else\n"), False)


def main():
    real_lock = P.LOCK
    check("the real lock file is not sitting there before the test",
          real_lock.exists(), False)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = project(Path(tmp) / "main")
            P.LOCK = root / "_work" / "daily.lock"     # never the real one
            the_plan(root)
            dry(root)
            lock(root)
            failure(root)
            tee_check(tmp)
            catch_up(tmp)
            after_filing(tmp)
            batch(tmp)
    finally:
        P.LOCK = real_lock

    check("the real _work/daily.lock was never created by this test",
          real_lock.exists(), False)

    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"(every step stubbed; no subprocess, no API call, no spend)")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
