"""
p2_tag.py -- Phase 2.3 Tag (code v0.2, 21 Oct 2026; prompt v0.1 unchanged)

WHAT IT DOES
  Gives every distinct article text of an extraction run a small fixed set of labels from a
  model and stores them in data/news.db, so Phase 3 can filter, group and follow stories
  without re-reading article text.
  Labels per text: topic (1 of 16), topics_more (0-2), kind, entities (name, type, canonical),
  one-sentence summary, text_check (extraction flag).

WHY
  Grouping ("everything on RBI this week") and following ("all coverage of Zoho") need a
  controlled vocabulary and named entities, not raw text.

WHAT CHANGED IN v0.2 (schema v1, see src/p2_store.py)
  Articles are read from news.db, not from the JSON files. Before any work, Tag imports the
  paper files in data/articles/ that are new or changed (p2_store.auto_import, guarded by
  git tag exam-v1). A tag record belongs to a TEXT (text hash), not to a seat: identical text
  in any seat or run is tagged once and never paid for twice. The prompt is unchanged (v0.1),
  so every Tag v0 record stays valid.

HOW TO RUN (always from ~/news-intel)
  .venv/bin/python src/p2_tag.py plan               import new papers (free, local), count texts, estimate cost; no API call
  .venv/bin/python src/p2_tag.py sample             tag a seeded 60-text sample now (normal API, ~2 min)
                                                    -> review/tag_audit/audit_60_<model>.jsonl for checking
  .venv/bin/python src/p2_tag.py run                tag every untagged text now with the normal API (~2x the batch
                                                    price). Ctrl+C is safe: re-run resumes.
  .venv/bin/python src/p2_tag.py submit             send every untagged text as ONE Batch API job
  .venv/bin/python src/p2_tag.py status             is the batch done yet?
  .venv/bin/python src/p2_tag.py collect            fetch batch results, validate, write news.db
  .venv/bin/python src/p2_tag.py stats              counts by SQL for one run: seats and texts tagged, topics, entities
  Options: --model haiku-4.5 (default) | haiku-5.5 · --run exam-v1 (default) | pre-freeze · --cap 25 (max dollars)
           --n 60 --seed 19 (sample) · --workers 6 (parallel calls) · --skip-import (tag only what is stored)

SAFETY
  Idempotent: a text is skipped if it already has a record for this model and prompt version.
  Fail-closed: an answer with an unknown topic or kind is rejected, not guessed.
  ET is not stored (p2_store). Reads news.db and data/articles/; writes news.db, data/batches/,
  review/tag_audit/ and logs/tag.log.
"""
import argparse, concurrent.futures as cf, datetime as dt, json, os, random, sys, time
from pathlib import Path

import p2_store as store

PROMPT_VERSION = "v0.1"
MODELS = {"haiku-4.5": "claude-haiku-4-5-20251001", "haiku-5.5": "claude-haiku-5-5"}
# Standard (non-batch) prices per million tokens, in / out. Batch API = half.
# Source: official pricing page, checked 15 Oct (drafts/2026-10-15_2.3_tag_design_draft.md).
PRICES = {"claude-haiku-4-5-20251001": (1.00, 5.00), "claude-haiku-5-5": (0.10, 0.50)}   # 5.5: prompts <= 100K tokens
# Per-model request settings (Haiku 5.5 migration guide, checked 19 Oct): Haiku 5.5 rejects temperature other
# than 1 (HTTP 400), so it gets none; with a forced tool it answers with the tool call and no thinking, so effort
# barely changes cost; medium is the default setting the model is tuned for. Its tokenizer gives ~30% more tokens for the same text (used in estimates).
SAMPLING = {"claude-haiku-4-5-20251001": {"temperature": 0}, "claude-haiku-5-5": {"output_config": {"effort": "medium"}}}
TOKEN_FACTOR = {"claude-haiku-4-5-20251001": 1.0, "claude-haiku-5-5": 1.3}
MAX_BODY_WORDS = 1200

TOPICS = ["Politics & Governance", "Economy & Policy", "Markets", "Companies & Business", "Banking & Finance",
          "Technology", "Energy & Environment", "Infrastructure & Transport", "Law & Courts", "Crime & Security",
          "Health", "Education", "World", "Science", "Sports", "Culture & Lifestyle"]
KINDS = ["news", "analysis", "opinion", "interview", "brief", "data", "sponsored", "notice", "other"]
ENTITY_TYPES = ["company", "person", "organisation", "place"]
TEXT_CHECKS = ["ok", "two_stories", "fragment", "garbled"]

SYSTEM = f"""You label articles from Indian national newspapers for a personal news archive.
Read the article and call the tool tag_article exactly once.

topic: the ONE best subject from this fixed list: {"; ".join(TOPICS)}.
topics_more: 0-2 further subjects from the same list, only if clearly central.
kind: news (straight report) | analysis (feature, explainer) | opinion (editorial, column, letter) |
  interview | brief (under ~80 words, one item) | data (scores, results, market wrap, tables) |
  sponsored (advertorial, "marketing initiative", promotional feature) | notice (obituary, tender, public notice) | other.
entities: the important named companies, people, organisations (incl. government bodies, regulators) and places
  that the article is about. name = as printed; canonical = the full standard name
  (e.g. "RBI" -> "Reserve Bank of India", "Modi" -> "Narendra Modi"). Do not list the newspaper itself or reporters.
summary: one neutral sentence, at most 30 words, in your own words.
text_check: ok | two_stories (text clearly mixes two unrelated stories) | fragment (starts or ends mid-story,
  or too little text to judge) | garbled (unreadable text)."""

TOOL = {
    "name": "tag_article",
    "description": "Record the labels for one article.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": TOPICS},
            "topics_more": {"type": "array", "items": {"type": "string", "enum": TOPICS}, "maxItems": 2},
            "kind": {"type": "string", "enum": KINDS},
            "entities": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string", "enum": ENTITY_TYPES},
                "canonical": {"type": "string"}}, "required": ["name", "type", "canonical"]}},
            "summary": {"type": "string"},
            "text_check": {"type": "string", "enum": TEXT_CHECKS},
        },
        "required": ["topic", "kind", "entities", "summary", "text_check"],
    },
}


# ---------- texts ----------
def open_run(args):
    """Open news.db (schema v1), bring the run up to date with its JSON folder, return the connection."""
    con = store.open_v1()
    if args.run not in store.RUNS:
        sys.exit(f"Unknown run '{args.run}'. Known: {sorted(store.RUNS)}")
    if not args.skip_import and store.RUNS[args.run][0] is not None:
        if not store.auto_import(con, args.run):
            sys.exit("Stopped: the store is not up to date (see above). Use --skip-import to tag only what is stored.")
    return con


def load_texts(con, run):
    """One item per distinct text in a run. Its prompt header (paper, date, deck) comes from the
    earliest seat holding that text (by date, then paper, then seat)."""
    out, seen = [], set()
    for row in con.execute(
            "SELECT p.text_hash, p.date, p.publisher, p.art_id, p.page, p.deck, x.headline, x.body, x.words "
            "FROM placements p JOIN texts x ON x.text_hash=p.text_hash WHERE p.run_id=? "
            "ORDER BY p.date, p.publisher, p.art_id", (run,)):
        h, date, pub, aid, page, deck, head, body, words = row
        if h in seen:
            continue
        seen.add(h)
        out.append({"text_hash": h, "key": f"{date}|{pub}|{aid}", "date": date, "publisher": pub, "page": page,
                    "deck": deck or "", "headline": head, "body": body, "words": words})
    return out


def seats_in_run(con, run):
    return con.execute("SELECT COUNT(*) FROM placements WHERE run_id=?", (run,)).fetchone()[0]


def user_text(a):
    words = a["body"].split()
    body = " ".join(words[:MAX_BODY_WORDS]) + (" [...]" if len(words) > MAX_BODY_WORDS else "")
    parts = [f"Newspaper: {a['publisher']}, {a['date']}", f"Headline: {a['headline'] or '(none)'}"]
    if a["deck"]:
        parts.append(f"Sub-headline: {a['deck']}")
    parts.append(f"Text:\n{body or '(no body text)'}")
    return "\n".join(parts)


def params(a, model):
    return {"model": model, "max_tokens": 1500, "system": SYSTEM, "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "tag_article"},
            "messages": [{"role": "user", "content": user_text(a)}]}


def todo(con, texts, model):
    """Texts without a record for this model and prompt version (idempotency)."""
    have = {h for (h,) in con.execute("SELECT text_hash FROM tags WHERE model=? AND prompt_version=?",
                                      (model, PROMPT_VERSION))}
    return [a for a in texts if a["text_hash"] not in have]


def validate(inp):
    """Fail-closed: unknown topic/kind -> None. Bad entities are dropped, not guessed."""
    if not isinstance(inp, dict) or inp.get("topic") not in TOPICS or inp.get("kind") not in KINDS:
        return None
    more = [t for t in (inp.get("topics_more") or []) if t in TOPICS and t != inp["topic"]][:2]
    ents, seen = [], set()
    for e in inp.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        typ = e.get("type")
        canon = str(e.get("canonical") or name).strip()
        if not name or typ not in ENTITY_TYPES or (canon.lower(), typ) in seen:
            continue
        seen.add((canon.lower(), typ))
        ents.append((name, typ, canon))
    tc = inp.get("text_check") if inp.get("text_check") in TEXT_CHECKS else "ok"
    return {"topic": inp["topic"], "topics_more": more, "kind": inp["kind"], "entities": ents,
            "summary": str(inp.get("summary") or "").strip()[:400], "text_check": tc}


def save_tag(con, h, model, tag, usage, via):
    con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, model, PROMPT_VERSION, tag["topic"], json.dumps(tag["topics_more"]), tag["kind"], tag["summary"],
                 tag["text_check"], dt.datetime.now().isoformat(timespec="seconds"), usage[0], usage[1], via))
    con.execute("DELETE FROM entities WHERE text_hash=? AND model=? AND prompt_version=?", (h, model, PROMPT_VERSION))
    con.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    [(h, model, PROMPT_VERSION) + e for e in tag["entities"]])


def tool_input(msg):
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


def cost(model, tin, tout, batch=False):
    pin, pout = PRICES[model]
    c = (tin * pin + tout * pout) / 1e6
    return c / 2 if batch else c


def estimate(texts, model, batch=False):
    words = sum(min(len(a["body"].split()), MAX_BODY_WORDS) for a in texts)
    f = TOKEN_FACTOR[model]
    tin = int((len(texts) * 1100 + words * 1.4) * f)  # ~1,100-token prompt + ~1.4 tokens per word
    tout = int(len(texts) * 220 * f)
    return tin, tout, cost(model, tin, tout, batch)


def log(line):
    Path("logs").mkdir(exist_ok=True)
    with open("logs/tag.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")


def make_client():
    from dotenv import load_dotenv
    import anthropic
    load_dotenv(Path.home() / ".config" / "news-intel" / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY found in ~/.config/news-intel/.env")
    return anthropic.Anthropic(max_retries=8)


# ---------- commands ----------
def cmd_plan(args, model):
    con = open_run(args)
    texts = load_texts(con, args.run)
    left = todo(con, texts, model)
    by = {}
    for a in texts:
        by[a["publisher"]] = by.get(a["publisher"], 0) + 1
    tin, tout, c_norm = estimate(left, model)
    print(f"run {args.run}: {seats_in_run(con, args.run)} seats, {len(texts)} distinct texts  {by}")
    print(f"already tagged with {model} {PROMPT_VERSION}: {len(texts) - len(left)} · still to tag: {len(left)}")
    print(f"estimate: ~{tin/1e6:.1f}M tokens in, ~{tout/1e6:.2f}M out -> "
          f"Batch API ${estimate(left, model, True)[2]:.2f} · normal API ${c_norm:.2f}")


def pick_sample(texts, n, seed):
    """Seeded, stratified: the same n/papers texts per paper every time (min 20 words)."""
    rng = random.Random(seed)
    pubs = sorted({a["publisher"] for a in texts})
    per = max(1, n // max(1, len(pubs)))
    out = []
    for p in pubs:
        pool = sorted([a for a in texts if a["publisher"] == p and (a["words"] or 0) >= 20], key=lambda a: a["key"])
        out += rng.sample(pool, min(per, len(pool)))
    return out


def tag_sync(con, texts, model, cap, workers, label):
    """Normal (non-batch) API calls in parallel. Saves every 50 answers, so Ctrl+C loses little;
    stops sending new work once the measured spend passes the cap."""
    import anthropic
    client = make_client()
    extra = {"on": True}

    def one(a):
        p = params(a, model)
        if extra["on"]:
            try:
                return a, client.messages.create(**p, extra_body=SAMPLING[model])
            except anthropic.BadRequestError as e:
                extra["on"] = False                  # setting refused by this model: fall back to defaults once
                print(f"  note: request settings {SAMPLING[model]} refused ({str(e)[:150]}); using defaults")
        return a, client.messages.create(**p)

    spent, ok, bad, done = 0.0, 0, 0, 0
    t0 = time.time()
    try:
        for start in range(0, len(texts), 50):
            if spent > cap:
                print(f"  stopped: spend ${spent:.2f} passed the cap ${cap:.2f}. Re-run to continue.")
                break
            chunk = texts[start:start + 50]
            con.execute("BEGIN")
            with cf.ThreadPoolExecutor(max_workers=workers) as ex:
                for fut in cf.as_completed([ex.submit(one, a) for a in chunk]):
                    done += 1
                    try:
                        a, msg = fut.result()
                        inp, usage = tool_input(msg), (msg.usage.input_tokens, msg.usage.output_tokens)
                    except Exception as e:
                        bad += 1
                        print(f"  call failed: {str(e)[:200]}")
                        continue
                    spent += cost(model, *usage)
                    tag = validate(inp)
                    if tag is None:
                        bad += 1
                        print(f"  rejected (stop_reason {getattr(msg, 'stop_reason', '?')}): {a['key']}")
                        continue
                    save_tag(con, a["text_hash"], model, tag, usage, "sync")
                    ok += 1
            con.execute("COMMIT")
            if len(texts) > 60:
                print(f"  {done}/{len(texts)} done · ${spent:.2f} · {(time.time() - t0) / 60:.1f} min")
    except KeyboardInterrupt:
        print("  interrupted: answers so far are saved; re-run to continue.")
    if con.in_transaction:
        con.execute("COMMIT")
    log(f"{label} model={model} n={len(texts)} ok={ok} bad={bad} cost=${spent:.4f}")
    print(f"tagged {ok}, failed/rejected {bad} (re-run retries them), cost ${spent:.4f}")


def cmd_sample(args, model):
    con = open_run(args)
    sample = pick_sample(load_texts(con, args.run), args.n, args.seed)
    left = todo(con, sample, model)
    print(f"sample: {len(sample)} texts from run {args.run} (seed {args.seed}); to tag now: {len(left)} with {model}")
    tag_sync(con, left, model, args.cap, args.workers, "sample")
    write_audit(con, sample, model)


def cmd_run(args, model):
    con = open_run(args)
    left = todo(con, load_texts(con, args.run), model)
    if not left:
        print(f"Nothing to tag: every text in run {args.run} already has a record for this model and prompt version.")
        return
    est = estimate(left, model)[2]
    print(f"{len(left)} texts with {model}, normal API; estimated ${est:.2f} (cap ${args.cap:.2f})")
    if est > args.cap:
        sys.exit("Estimate is above --cap; nothing sent. Raise --cap if you mean it.")
    tag_sync(con, left, model, args.cap, args.workers, f"run[{args.run}]")


def write_audit(con, sample, model):
    Path("review/tag_audit").mkdir(parents=True, exist_ok=True)
    short = [k for k, v in MODELS.items() if v == model][0]
    path = Path(f"review/tag_audit/audit_{len(sample)}_{short}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for i, a in enumerate(sorted(sample, key=lambda a: a["key"]), 1):
            row = con.execute("SELECT topic, topics_more, kind, summary, text_check FROM tags "
                              "WHERE text_hash=? AND model=? AND prompt_version=?",
                              (a["text_hash"], model, PROMPT_VERSION)).fetchone()
            ents = con.execute("SELECT name, type, canonical FROM entities WHERE text_hash=? AND model=? "
                               "AND prompt_version=?", (a["text_hash"], model, PROMPT_VERSION)).fetchall()
            f.write(json.dumps({
                "n": f"A{i}", "key": a["key"], "text_hash": a["text_hash"], "words": a["words"],
                "headline": a["headline"], "body": a["body"][:1500], "topic": row[0] if row else None,
                "topics_more": json.loads(row[1]) if row else None, "kind": row[2] if row else None,
                "summary": row[3] if row else None, "text_check": row[4] if row else None,
                "entities": [{"name": n, "type": t, "canonical": c} for n, t, c in ents],
            }, ensure_ascii=False) + "\n")
    print(f"audit file: {path}  (upload this file to the chat)")


def cmd_submit(args, model):
    con = open_run(args)
    open_b = con.execute("SELECT batch_id FROM batches WHERE status != 'collected'").fetchall()
    if open_b:
        sys.exit(f"A batch is still open: {open_b[0][0]}. Run status / collect first.")
    left = todo(con, load_texts(con, args.run), model)
    if not left:
        print(f"Nothing to tag: every text in run {args.run} already has a record for this model and prompt version.")
        return
    est = estimate(left, model, batch=True)[2]
    print(f"{len(left)} texts -> one Batch API job with {model}; estimated ${est:.2f} (cap ${args.cap:.2f})")
    if est > args.cap:
        sys.exit("Estimate is above --cap; nothing sent. Raise --cap if you mean it.")
    # custom_id = "t" + text hash: unique by construction (one request per distinct text)
    reqs = [{"custom_id": "t" + a["text_hash"], "params": dict(params(a, model), **SAMPLING[model])} for a in left]
    client = make_client()
    batch = client.messages.batches.create(requests=reqs)
    Path("data/batches").mkdir(parents=True, exist_ok=True)
    with open(f"data/batches/{batch.id}.jsonl", "w", encoding="utf-8") as f:
        for a in left:
            f.write(json.dumps({"custom_id": "t" + a["text_hash"], "text_hash": a["text_hash"], "key": a["key"]}) + "\n")
    con.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
                (batch.id, model, PROMPT_VERSION, dt.datetime.now().isoformat(timespec="seconds"), len(reqs),
                 "submitted", None))
    log(f"submit batch={batch.id} model={model} run={args.run} requests={len(reqs)} est=${est:.2f}")
    print(f"submitted batch {batch.id} ({len(reqs)} requests). Check with: status")


def cmd_status(args, model):
    con = store.open_v1()
    rows = con.execute("SELECT batch_id, requests, submitted_at, status FROM batches ORDER BY submitted_at").fetchall()
    if not rows:
        print("No batches yet.")
        return
    client = make_client()
    for bid, n, sub, st in rows:
        if st == "collected":
            print(f"{bid}: collected ({n} requests, submitted {sub})")
            continue
        b = client.messages.batches.retrieve(bid)
        rc = b.request_counts
        print(f"{bid}: {b.processing_status} · processing {rc.processing}, succeeded {rc.succeeded}, "
              f"errored {rc.errored}, expired {rc.expired} (submitted {sub})")


def cmd_collect(args, model):
    con = store.open_v1()
    rows = con.execute("SELECT batch_id, model, prompt_version FROM batches WHERE status != 'collected'").fetchall()
    if not rows:
        print("No open batch to collect.")
        return
    client = make_client()
    for bid, bmodel, bpv in rows:
        if bpv != PROMPT_VERSION:
            print(f"{bid}: submitted with prompt {bpv}, this code is {PROMPT_VERSION}; skipped.")
            continue
        b = client.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            print(f"{bid}: still {b.processing_status}; try again later.")
            continue
        mapping = {}
        with open(f"data/batches/{bid}.jsonl", encoding="utf-8") as f:
            for line in f:
                m = json.loads(line)
                mapping[m["custom_id"]] = m["text_hash"]
        ok = bad = 0
        tin = tout = 0
        con.execute("BEGIN")
        for entry in client.messages.batches.results(bid):
            h = mapping.get(entry.custom_id)
            if not h or entry.result.type != "succeeded":
                bad += 1
                continue
            msg = entry.result.message
            tin += msg.usage.input_tokens
            tout += msg.usage.output_tokens
            tag = validate(tool_input(msg))
            if tag is None:
                bad += 1
                continue
            save_tag(con, h, bmodel, tag, (msg.usage.input_tokens, msg.usage.output_tokens), "batch")
            ok += 1
        con.execute("UPDATE batches SET status='collected', collected_at=? WHERE batch_id=?",
                    (dt.datetime.now().isoformat(timespec="seconds"), bid))
        con.execute("COMMIT")
        spent = cost(bmodel, tin, tout, batch=True)
        log(f"collect batch={bid} ok={ok} bad={bad} tokens_in={tin} tokens_out={tout} cost=${spent:.4f}")
        print(f"{bid}: tagged {ok}, failed/rejected {bad} (re-sent by the next submit); cost ${spent:.2f}")


def cmd_stats(args, model):
    con = store.open_v1()
    v = store.v1_numbers(con, args.run).get((model, PROMPT_VERSION))
    print(f"run {args.run} · model {model}, prompt {PROMPT_VERSION}")
    print(f"seats in run: {seats_in_run(con, args.run)} · distinct texts: {len(load_texts(con, args.run))}")
    if not v:
        print("nothing tagged yet for this run, model and prompt version.")
        return
    print(f"seats tagged (N): {v['N']} · distinct texts tagged: {v['texts']}")
    print(f"unique entities (E, by canonical name, case-insensitive): {v['E']} · entity mentions: {v['mentions']}")
    print(f"entities in 3+ papers (C): {v['C']}")
    J = ("FROM placements pl JOIN tags t ON t.text_hash=pl.text_hash WHERE pl.run_id=? AND t.model=? "
         "AND t.prompt_version=?")
    E = ("FROM placements pl JOIN entities e ON e.text_hash=pl.text_hash WHERE pl.run_id=? AND e.model=? "
         "AND e.prompt_version=?")
    p = (args.run, model, PROMPT_VERSION)
    for label, sql in [
        ("topics used (K)", f"SELECT COUNT(DISTINCT t.topic) {J}"),
        ("by type", f"SELECT e.type, COUNT(DISTINCT lower(e.canonical)) {E} GROUP BY 1 ORDER BY 2 DESC"),
        ("by topic", f"SELECT t.topic, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("by kind", f"SELECT t.kind, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("text_check", f"SELECT t.text_check, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("top entities (seats)", f"SELECT e.canonical, COUNT(DISTINCT pl.date||pl.publisher||pl.art_id) {E} "
                                 f"GROUP BY lower(e.canonical) ORDER BY 2 DESC LIMIT 10"),
    ]:
        print(f"{label}: {con.execute(sql, p).fetchall()}")
    print("cost per run: logs/tag.log (cost= fields)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "sample", "run", "submit", "status", "collect", "stats"])
    ap.add_argument("--model", default="haiku-4.5", choices=sorted(MODELS))
    ap.add_argument("--run", default=store.CURRENT_RUN)
    ap.add_argument("--cap", type=float, default=25.0, help="max estimated dollars per run/submit")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--skip-import", action="store_true", help="do not import new/changed papers first")
    args = ap.parse_args()
    model = MODELS[args.model]
    {"plan": cmd_plan, "sample": cmd_sample, "run": cmd_run, "submit": cmd_submit, "status": cmd_status,
     "collect": cmd_collect, "stats": cmd_stats}[args.command](args, model)


if __name__ == "__main__":
    main()
