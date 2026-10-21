"""
p2_store.py -- the article store in data/news.db (schema v1, 21 Oct 2026)

WHAT IT DOES
  Keeps every extracted article text in data/news.db, once per distinct text, and records
  which extraction version (run) put which text in which seat (date | paper | pNN-aNN).
  Tag (and later Embed and Phase 3) read articles from here, never from the JSON files.

WHY
  Until now an article was identified by its seat. A re-extraction reshuffles seats, so new
  tags overwrote old ones and identical text was paid for again. Now:
    text_hash  = SHA-1 of headline + newline + body (same formula as Tag v0)  -> identity
    texts      = one row per distinct text (headline, body, words)
    runs       = extraction versions: 'pre-freeze' (15 Oct), 'exam-v1' (frozen code, git tag exam-v1)
    placements = run x seat -> text_hash (+ page, deck, byline, other article fields)
    imports    = which JSON file was imported into which run, with the file's own fingerprint
    tags, entities = labels keyed by text_hash + model + prompt version (one record per combination)

HOW TO RUN (always from ~/news-intel)
  .venv/bin/python src/p2_store.py migrate            REHEARSAL: builds the v1 layout from the v0 tables and the
                                                      pre-freeze JSON, prints every check, then ROLLS BACK (no change)
  .venv/bin/python src/p2_store.py migrate --apply    the same, and keeps the result if every required check passes
  .venv/bin/python src/p2_store.py import             import new/changed papers from data/articles/ into run exam-v1
                                                      (Tag does this by itself; this is the manual way)
  .venv/bin/python src/p2_store.py check              per-run counts; while v0 tables exist, old vs new numbers
  .venv/bin/python src/p2_store.py drop-v0 --yes      after the check passes: delete the renamed v0_* tables
  Options: --from <folder> (migrate: pre-freeze JSON) · --run exam-v1 · --folder data/articles

SAFETY
  migrate: one transaction; without --apply it always rolls back; with --apply it commits only if
    every required check passes. Take a snapshot first anyway (commands in STATUS).
  Merge rule: v0 tagged identical text in several seats; v1 keeps one record per text, model and
    prompt version: the record of the EARLIEST seat (by date, then paper, then seat). Dropped ones are listed.
  Import guard (run exam-v1): refuses if the extraction code differs from git tag exam-v1
    (uncommitted edits included), so text from changed code can never be filed as exam-v1.
  Import is idempotent: a paper is re-imported only when its JSON file's fingerprint changed;
    a re-imported paper replaces its own seats in that run and nothing else. Texts are never deleted.
  Economic Times is not stored (decided 21 Oct).
"""
import argparse, datetime as dt, glob, hashlib, json, sqlite3, subprocess, sys
from pathlib import Path

SCHEMA_VERSION = "1"
DB = Path("data") / "news.db"
ARTICLES = Path("data") / "articles"
PREFREEZE = Path("_old") / "articles_before_exam-v1_2026-10-20"
CURRENT_RUN = "exam-v1"
RUNS = {
    "pre-freeze": (None, "15 Oct extraction, before the freeze; exact code commit unknown. "
                         "Text from _old/articles_before_exam-v1_2026-10-20/. Tag v0 numbers belong to this run."),
    "exam-v1": ("exam-v1", "Frozen extraction (rules v3.2 + Screen v0.2 + per-paper settings), git tag exam-v1."),
}
EXTRACT_CODE = ["src/p2_extract.py", "src/p2_extract_rules.py", "src/p2_extract_referee.py", "src/p2_screen.py"]
EXCLUDE = {"Economic Times"}
COLUMNS = {"date", "publisher", "id", "page", "number", "headline", "deck", "byline", "body", "words"}

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, code_ref TEXT, note TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS texts (text_hash TEXT PRIMARY KEY, headline TEXT, body TEXT, words INTEGER, "
    "first_run TEXT, first_seen TEXT)",
    "CREATE TABLE IF NOT EXISTS placements (run_id TEXT, date TEXT, publisher TEXT, art_id TEXT, page INTEGER, "
    "number INTEGER, deck TEXT, byline TEXT, text_hash TEXT, extra TEXT, source_file TEXT, imported_at TEXT, "
    "PRIMARY KEY (run_id, date, publisher, art_id))",
    "CREATE INDEX IF NOT EXISTS pl_hash ON placements(text_hash)",
    "CREATE INDEX IF NOT EXISTS pl_file ON placements(run_id, source_file)",
    "CREATE TABLE IF NOT EXISTS imports (run_id TEXT, source_file TEXT, file_hash TEXT, articles INTEGER, "
    "imported_at TEXT, PRIMARY KEY (run_id, source_file))",
    "CREATE TABLE IF NOT EXISTS tags (text_hash TEXT, model TEXT, prompt_version TEXT, topic TEXT, topics_more TEXT, "
    "kind TEXT, summary TEXT, text_check TEXT, tagged_at TEXT, in_tokens INTEGER, out_tokens INTEGER, via TEXT, "
    "PRIMARY KEY (text_hash, model, prompt_version))",
    "CREATE TABLE IF NOT EXISTS entities (text_hash TEXT, model TEXT, prompt_version TEXT, name TEXT, type TEXT, "
    "canonical TEXT)",
    "CREATE INDEX IF NOT EXISTS ent_hash ON entities(text_hash, model, prompt_version)",
    "CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, model TEXT, prompt_version TEXT, "
    "submitted_at TEXT, requests INTEGER, status TEXT, collected_at TEXT)",
]


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def text_hash(head, body):
    return hashlib.sha1((head + "\n" + body).encode("utf-8")).hexdigest()


def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def connect():
    """Open news.db with manual transactions (we issue BEGIN / COMMIT / ROLLBACK ourselves)."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB, isolation_level=None)


def schema_version(con):
    if "meta" not in tables(con):
        return None
    row = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
    return row[0] if row else None


def open_v1():
    """For Tag and other readers: the database must already be on schema v1."""
    con = connect()
    v = schema_version(con)
    if v is None and "tags" in tables(con):
        sys.exit("news.db is still on the v0 layout. Run: .venv/bin/python src/p2_store.py migrate (then --apply)")
    if v is None:                                         # brand-new empty database
        con.execute("BEGIN")
        create_schema(con)
        con.execute("COMMIT")
    return con


def create_schema(con):
    for stmt in SCHEMA:
        con.execute(stmt)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    for rid, (ref, note) in RUNS.items():
        con.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?)", (rid, ref, note, now()))


# ---------- reading JSON ----------
def read_paper(path):
    """One 2.1 JSON file -> list of seats, using exactly Tag v0's key and hash rules."""
    name = Path(path).stem                                 # 2026-10-13_The Hindu
    fdate, fpub = name[:10], name[11:]
    arts = json.load(open(path, encoding="utf-8"))
    out, seen, dup_ids = [], set(), 0
    for a in arts:
        pub = a.get("publisher") or fpub
        if pub in EXCLUDE or fpub in EXCLUDE:
            continue
        head, body = (a.get("headline") or "").strip(), (a.get("body") or "").strip()
        if not head and not body:
            continue
        date = a.get("date") or fdate
        aid = str(a.get("id") or f"p{a.get('page', 0):02d}-a{a.get('number', 0):02d}")
        if (date, pub, aid) in seen:                       # same seat twice in one file: keep the first
            dup_ids += 1
            continue
        seen.add((date, pub, aid))
        extra = {k: v for k, v in a.items() if k not in COLUMNS and v not in (None, "", [], {})}
        out.append({"date": date, "publisher": pub, "art_id": aid, "page": a.get("page"), "number": a.get("number"),
                    "headline": head, "deck": (a.get("deck") or "").strip(), "byline": (a.get("byline") or "").strip(),
                    "body": body, "words": a.get("words") or len(body.split()),
                    "text_hash": text_hash(head, body), "extra": json.dumps(extra, ensure_ascii=False)})
    return out, dup_ids


def paper_files(folder):
    """(relative name, path) for every paper JSON in a folder, ET files skipped."""
    out = []
    for f in sorted(glob.glob(str(Path(folder) / "*" / "*.json"))):
        if Path(f).stem[11:] in EXCLUDE:
            continue
        out.append((str(Path(f).relative_to(folder)), f))
    return out


def file_hash(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


# ---------- import ----------
def code_matches(ref):
    """True if the extraction code is identical to git tag `ref` (uncommitted edits count)."""
    try:
        r = subprocess.run(["git", "diff", "--quiet", ref, "--"] + EXTRACT_CODE, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "git not found"
    if r.returncode == 0:
        return True, ""
    if r.returncode == 1:
        d = subprocess.run(["git", "diff", "--stat", ref, "--"] + EXTRACT_CODE, capture_output=True, text=True)
        return False, d.stdout.strip()
    return False, (r.stderr or "git diff failed").strip()


def pending_files(con, run_id, folder):
    """Paper files that are new, or whose content changed since their last import into this run."""
    done = dict(con.execute("SELECT source_file, file_hash FROM imports WHERE run_id=?", (run_id,)))
    out = []
    for rel, path in paper_files(folder):
        h = file_hash(path)
        if done.get(rel) != h:
            out.append((rel, path, h, rel in done))
    return out


def import_files(con, run_id, files):
    """Write seats for the given files into a run. Caller holds the transaction."""
    seats = new_texts = dup_ids = 0
    for rel, path, fh, _ in files:
        rows, d = read_paper(path)
        dup_ids += d
        con.execute("DELETE FROM placements WHERE run_id=? AND source_file=?", (run_id, rel))
        t = now()
        for a in rows:
            cur = con.execute("INSERT OR IGNORE INTO texts VALUES (?,?,?,?,?,?)",
                              (a["text_hash"], a["headline"], a["body"], a["words"], run_id, t))
            new_texts += cur.rowcount
            con.execute("INSERT OR REPLACE INTO placements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, a["date"], a["publisher"], a["art_id"], a["page"], a["number"], a["deck"],
                         a["byline"], a["text_hash"], a["extra"], rel, t))
        con.execute("INSERT OR REPLACE INTO imports VALUES (?,?,?,?,?)", (run_id, rel, fh, len(rows), t))
        seats += len(rows)
    return seats, new_texts, dup_ids


def auto_import(con, run_id=CURRENT_RUN, folder=ARTICLES, quiet=False):
    """Bring a run up to date with its JSON folder. Used by Tag before it does anything.
    Returns True if the run is up to date afterwards; False if the guard refused."""
    if run_id not in RUNS or RUNS[run_id][0] is None:
        sys.exit(f"Run '{run_id}' cannot be imported into (known runs with code: "
                 f"{[r for r, v in RUNS.items() if v[0]]}).")
    files = pending_files(con, run_id, folder)
    if not files:
        if not quiet:
            print(f"store: run {run_id} is up to date with {folder}/")
        return True
    ok, why = code_matches(RUNS[run_id][0])
    if not ok:
        print(f"store: {len(files)} new/changed paper file(s) in {folder}/, but the extraction code differs from "
              f"git tag {RUNS[run_id][0]}; NOT imported.\n  {why}\n  Restore the code, or add a new run name.")
        return False
    con.execute("BEGIN IMMEDIATE")
    try:
        seats, new_texts, dup_ids = import_files(con, run_id, files)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    changed = sum(1 for f in files if f[3])
    print(f"store: imported {len(files)} paper file(s) into {run_id} ({len(files) - changed} new, {changed} changed): "
          f"{seats} seats, {new_texts} texts not seen before" + (f", {dup_ids} duplicate seat ids skipped" if dup_ids else ""))
    return True


# ---------- migrate v0 -> v1 ----------
def v0_numbers(con, prefix):
    """Tag v0 headline numbers, computed exactly as v0 `stats` did, on tables with the given prefix."""
    out = {}
    for model, pv in con.execute(f"SELECT DISTINCT model, prompt_version FROM {prefix}tags ORDER BY model"):
        p = (model, pv)
        n = con.execute(f"SELECT COUNT(*) FROM {prefix}tags WHERE model=? AND prompt_version=?", p).fetchone()[0]
        e = con.execute(f"SELECT COUNT(DISTINCT lower(canonical)) FROM {prefix}entities WHERE model=? AND prompt_version=?",
                        p).fetchone()[0]
        m = con.execute(f"SELECT COUNT(*) FROM {prefix}entities WHERE model=? AND prompt_version=?", p).fetchone()[0]
        c = con.execute(f"SELECT COUNT(*) FROM (SELECT lower(e.canonical) c FROM {prefix}entities e JOIN {prefix}articles a "
                        f"ON a.key=e.key WHERE e.model=? AND e.prompt_version=? GROUP BY c "
                        f"HAVING COUNT(DISTINCT a.publisher) >= 3)", p).fetchone()[0]
        out[p] = {"N": n, "E": e, "mentions": m, "C": c}
    return out


def v1_numbers(con, run_id):
    """The same numbers on the v1 layout, for one run, counting seats (as v0 did)."""
    out = {}
    q = lambda sql, p: con.execute(sql, p).fetchone()[0]
    for model, pv in con.execute("SELECT DISTINCT model, prompt_version FROM tags ORDER BY model").fetchall():
        p = (run_id, model, pv)
        base = ("FROM placements pl JOIN tags t ON t.text_hash=pl.text_hash AND t.model=? AND t.prompt_version=? "
                "WHERE pl.run_id=?")
        n = q(f"SELECT COUNT(*) {base}", (model, pv, run_id))
        texts = q(f"SELECT COUNT(DISTINCT pl.text_hash) {base}", (model, pv, run_id))
        ent = ("FROM placements pl JOIN entities e ON e.text_hash=pl.text_hash AND e.model=? AND e.prompt_version=? "
               "WHERE pl.run_id=?")
        e = q(f"SELECT COUNT(DISTINCT lower(e.canonical)) {ent}", (model, pv, run_id))
        m = q(f"SELECT COUNT(*) {ent}", (model, pv, run_id))
        c = q(f"SELECT COUNT(*) FROM (SELECT lower(e.canonical) c {ent} GROUP BY c "
              f"HAVING COUNT(DISTINCT pl.publisher) >= 3)", (model, pv, run_id))
        if n:
            out[(model, pv)] = {"N": n, "texts": texts, "E": e, "mentions": m, "C": c}
    return out


def cmd_migrate(args):
    src = Path(args.src)
    if not DB.exists():
        sys.exit(f"{DB} not found. Run from ~/news-intel.")
    con = connect()
    if schema_version(con):
        sys.exit("news.db is already on schema v1; nothing to migrate.")
    have = tables(con)
    need = {"articles", "tags", "entities"}
    if not need <= have:
        sys.exit(f"v0 tables missing: {sorted(need - have)}")
    cols = [r[1] for r in con.execute("PRAGMA table_info(tags)")]
    if "key" not in cols:
        sys.exit("The tags table does not have the v0 layout (no 'key' column); stopping.")
    files = [(rel, p, file_hash(p), False) for rel, p in paper_files(src)]
    if not files:
        sys.exit(f"No paper JSON files in {src}/")

    print(f"{'REHEARSAL (rolls back at the end)' if not args.apply else 'APPLY'} · v0 -> v1 · pre-freeze text from {src}/")
    con.execute("BEGIN IMMEDIATE")
    ok = False
    try:
        before = v0_numbers(con, "")
        for t in ("articles", "tags", "entities"):
            con.execute(f"ALTER TABLE {t} RENAME TO v0_{t}")
        create_schema(con)
        seats, new_texts, dup_ids = import_files(con, "pre-freeze", files)
        print(f"1 import   {len(files)} paper files -> {seats} seats, {new_texts} distinct texts"
              + (f", {dup_ids} duplicate seat ids skipped" if dup_ids else ""))

        # v0 seats must be found in pre-freeze with the same text
        v0_seats = con.execute("SELECT COUNT(*) FROM v0_articles").fetchone()[0]
        missing = con.execute(
            "SELECT a.key FROM v0_articles a LEFT JOIN placements p ON p.run_id='pre-freeze' AND "
            "a.key = p.date || '|' || p.publisher || '|' || p.art_id AND p.text_hash=a.text_hash "
            "WHERE p.text_hash IS NULL").fetchall()
        print(f"2 seats    v0 articles {v0_seats}; found in pre-freeze with identical text: {v0_seats - len(missing)}; "
              f"missing or different: {len(missing)}  [required: 0]")
        for (k,) in missing[:10]:
            print(f"           {k}")

        # merge rule: one record per (text, model, prompt version), the earliest seat wins
        groups = con.execute(
            "SELECT text_hash, model, prompt_version, COUNT(*) FROM v0_tags GROUP BY 1,2,3").fetchall()
        kept = dropped = 0
        merged = []
        for h, model, pv, n in groups:
            rows = con.execute("SELECT key, topic, kind, text_check, topics_more, summary, tagged_at, in_tokens, "
                               "out_tokens, via FROM v0_tags WHERE text_hash=? AND model=? AND prompt_version=? "
                               "ORDER BY key", (h, model, pv)).fetchall()
            win = rows[0]
            con.execute("INSERT INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (h, model, pv, win[1], win[4], win[2], win[5], win[3], win[6], win[7], win[8], win[9]))
            con.execute("INSERT INTO entities SELECT ?, model, prompt_version, name, type, canonical FROM v0_entities "
                        "WHERE key=? AND model=? AND prompt_version=?", (h, win[0], model, pv))
            kept += 1
            if n > 1:
                dropped += n - 1
                merged.append((model, h, rows))
        v0_tag_rows = con.execute("SELECT COUNT(*) FROM v0_tags").fetchone()[0]
        print(f"3 merge    v0 tag records {v0_tag_rows} -> v1 records {kept}; dropped {dropped} "
              f"(same text, model and prompt in several seats; earliest seat kept)")
        for model, h, rows in merged:
            print(f"           {h[:8]} {model}: keep {rows[0][0]} [{rows[0][1]} / {rows[0][2]} / {rows[0][3]}]")
            for r in rows[1:]:
                print(f"                    drop {r[0]} [{r[1]} / {r[2]} / {r[3]}]")

        orphans = con.execute("SELECT COUNT(*) FROM tags t LEFT JOIN texts x ON x.text_hash=t.text_hash "
                              "WHERE x.text_hash IS NULL").fetchone()[0]
        lost = con.execute("SELECT COUNT(*) FROM v0_tags o LEFT JOIN tags t ON t.text_hash=o.text_hash AND "
                           "t.model=o.model AND t.prompt_version=o.prompt_version WHERE t.text_hash IS NULL").fetchone()[0]
        print(f"4 links    v1 records whose text is not stored: {orphans}  [required: 0] · "
              f"v0-tagged seats left without a v1 record: {lost}  [required: 0]")

        after = v1_numbers(con, "pre-freeze")
        print("5 numbers  Tag v0 (old tables, old queries) vs v1 (run pre-freeze, new tables)")
        for p, b in before.items():
            a = after.get(p, {})
            gain = (a.get("N") or 0) - b["N"]
            print(f"           {p[0]} {p[1]}: N {b['N']} -> {a.get('N')}"
                  f"{'' if gain == 0 else f' (+{gain}: untagged seats sharing text with a tagged seat)'} · "
                  f"E {b['E']} -> {a.get('E')} · C {b['C']} -> {a.get('C')} · mentions {b['mentions']} -> {a.get('mentions')}")
        print("           (N can only stay or rise; E and C can fall only by what the dropped records alone named;\n"
              "            mentions now count per seat, so a seat that lost its own record counts the kept record's entities)")

        ok = not missing and orphans == 0 and lost == 0
        print(f"required checks: {'ALL PASS' if ok else 'FAILED'}")
        if args.apply and ok:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('migrated_at', ?)", (now(),))
            con.execute("COMMIT")
            print("committed. Old tables kept as v0_articles, v0_tags, v0_entities until `drop-v0 --yes`.")
        else:
            con.execute("ROLLBACK")
            print("rolled back: news.db is exactly as before." +
                  ("" if args.apply else " Re-run with --apply to keep the result.") +
                  ("" if ok else " Nothing was changed because a required check failed."))
    except BaseException:
        con.execute("ROLLBACK")
        print("error: rolled back; news.db is exactly as before.")
        raise


# ---------- other commands ----------
def cmd_import(args):
    con = open_v1()
    auto_import(con, args.run, Path(args.folder))


def cmd_check(args):
    con = open_v1()
    print(f"schema v{schema_version(con)} · {DB}")
    for rid, ref, note in con.execute("SELECT run_id, code_ref, note FROM runs ORDER BY created_at, run_id").fetchall():
        files, seats, texts = con.execute(
            "SELECT COUNT(DISTINCT source_file), COUNT(*), COUNT(DISTINCT text_hash) FROM placements WHERE run_id=?",
            (rid,)).fetchone()
        by = con.execute("SELECT publisher, COUNT(*) FROM placements WHERE run_id=? GROUP BY 1 ORDER BY 1",
                         (rid,)).fetchall()
        print(f"run {rid} (code {ref or 'unknown'}): {files} papers, {seats} seats, {texts} distinct texts  {dict(by)}")
        for (model, pv), v in v1_numbers(con, rid).items():
            print(f"    tagged by {model} {pv}: {v['N']} seats ({v['texts']} texts) · E {v['E']} · C {v['C']} · "
                  f"mentions {v['mentions']}")
    shared = con.execute("SELECT COUNT(DISTINCT a.text_hash) FROM placements a JOIN placements b "
                         "ON a.text_hash=b.text_hash AND a.run_id='pre-freeze' AND b.run_id='exam-v1'").fetchone()[0]
    print(f"texts present in both pre-freeze and exam-v1 (identical, never tagged twice): {shared}")
    pend = pending_files(con, CURRENT_RUN, ARTICLES) if ARTICLES.exists() else []
    print(f"paper files in {ARTICLES}/ not yet imported into {CURRENT_RUN}: {len(pend)}")
    if "v0_tags" in tables(con):
        print("v0 tables still present (Tag v0 numbers, old queries):")
        for p, b in v0_numbers(con, "v0_").items():
            print(f"    {p[0]} {p[1]}: N {b['N']} · E {b['E']} · C {b['C']} · mentions {b['mentions']}")


def cmd_drop_v0(args):
    con = open_v1()
    old = [t for t in ("v0_articles", "v0_tags", "v0_entities") if t in tables(con)]
    if not old:
        print("No v0 tables left.")
        return
    if not args.yes:
        sys.exit(f"Would delete {old}. They also live in the snapshots. Add --yes to do it.")
    con.execute("BEGIN IMMEDIATE")
    for t in old:
        con.execute(f"DROP TABLE {t}")
    con.execute("COMMIT")
    con.execute("VACUUM")
    print(f"deleted {old}; file compacted.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["migrate", "import", "check", "drop-v0"])
    ap.add_argument("--apply", action="store_true", help="migrate: keep the result (default: rehearse and roll back)")
    ap.add_argument("--from", dest="src", default=str(PREFREEZE), help="migrate: folder with the pre-freeze JSON")
    ap.add_argument("--run", default=CURRENT_RUN)
    ap.add_argument("--folder", default=str(ARTICLES))
    ap.add_argument("--yes", action="store_true", help="drop-v0: confirm")
    args = ap.parse_args()
    {"migrate": cmd_migrate, "import": cmd_import, "check": cmd_check, "drop-v0": cmd_drop_v0}[args.command](args)


if __name__ == "__main__":
    main()
