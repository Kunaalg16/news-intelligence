"""
p2_tag.py -- Phase 2.3 Tag (v0.1, 19 Oct 2026)

WHAT IT DOES
  Gives every article from 2.1 a small fixed set of labels from a model and
  stores them in data/news.db (SQLite), so Phase 3 can filter, group and
  follow stories without re-reading article text.
  Labels per article: topic (1 of 16), topics_more (0-2), kind, entities
  (name, type, canonical), one-sentence summary, text_check (extraction flag).

WHY
  Grouping ("everything on RBI this week") and following ("all coverage of
  Zoho") need a controlled vocabulary and named entities, not raw text.

HOW TO RUN (always from ~/news-intel)
  .venv/bin/python src/p2_tag.py plan               count articles + cost estimate; calls nothing, writes nothing
  .venv/bin/python src/p2_tag.py sample             tag a seeded 60-article sample now (normal API, ~2 min)
                                                    -> review/tag_audit/audit_60_<model>.jsonl for checking
  .venv/bin/python src/p2_tag.py run                tag every untagged article now with the normal API (no waiting
                                                    for a batch; ~2x the batch price). Ctrl+C is safe: re-run resumes.
  .venv/bin/python src/p2_tag.py submit             send every untagged article as ONE Batch API job
  .venv/bin/python src/p2_tag.py status             is the batch done yet?
  .venv/bin/python src/p2_tag.py collect            fetch batch results, validate, write news.db
  .venv/bin/python src/p2_tag.py stats              counts by SQL: articles tagged, topics, unique entities
  Options: --model haiku-4.5 (default) | haiku-5.5 · --cap 25 (max dollars per run) · --n 60 --seed 19 (sample) · --workers 6 (parallel calls)

SAFETY
  Idempotent: an article is skipped if it already has a tag with the same
  text hash, model and prompt version (never paid twice).
  Fail-closed: an answer with an unknown topic or kind is rejected, not guessed.
  ET is excluded (decided at Tag). Reads data/articles/, writes data/news.db,
  data/batches/, review/tag_audit/ and logs/tag.log.
"""
import argparse, concurrent.futures as cf, datetime as dt, glob, hashlib, json, os, random, re, sqlite3, sys, time
from pathlib import Path

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
EXCLUDE = {"Economic Times"}
CODES = {"Times of India": "TOI", "The Hindu": "TH", "Mint": "MINT", "Business Line": "BL", "Business Standard": "BS"}
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

ROOT = Path(".")
DB = ROOT / "data" / "news.db"


# ---------- articles ----------
def load_articles():
    """Every 2.1 article except ET, with a stable key and a content hash."""
    out = []
    for f in sorted(glob.glob(str(ROOT / "data" / "articles" / "*" / "*.json"))):
        name = Path(f).stem                      # 2026-10-13_The Hindu
        fdate, fpub = name[:10], name[11:]
        try:
            arts = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  skipped unreadable file {f}: {e}")
            continue
        for a in arts:
            pub = a.get("publisher") or fpub
            if pub in EXCLUDE or fpub in EXCLUDE:
                continue
            head, body = (a.get("headline") or "").strip(), (a.get("body") or "").strip()
            if not head and not body:
                continue
            date = a.get("date") or fdate
            aid = str(a.get("id") or f"p{a.get('page', 0):02d}-a{a.get('number', 0):02d}")
            out.append({
                "key": f"{date}|{pub}|{aid}", "date": date, "publisher": pub, "art_id": aid,
                "page": a.get("page"), "headline": head, "deck": (a.get("deck") or "").strip(), "body": body,
                "words": a.get("words") or len(body.split()),
                "text_hash": hashlib.sha1((head + "\n" + body).encode("utf-8")).hexdigest(),
            })
    return out


def custom_id(a):
    code = CODES.get(a["publisher"]) or re.sub(r"[^A-Za-z0-9]", "", a["publisher"])[:8]
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", f"{a['date'].replace('-', '')}-{code}-{a['art_id']}")
    return cid[:64]


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


# ---------- database ----------
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS articles (key TEXT PRIMARY KEY, date TEXT, publisher TEXT, art_id TEXT, page INTEGER,
        headline TEXT, words INTEGER, text_hash TEXT);
    CREATE TABLE IF NOT EXISTS tags (key TEXT, text_hash TEXT, model TEXT, prompt_version TEXT,
        topic TEXT, topics_more TEXT, kind TEXT, summary TEXT, text_check TEXT, tagged_at TEXT,
        in_tokens INTEGER, out_tokens INTEGER, via TEXT, PRIMARY KEY (key, model, prompt_version));
    CREATE TABLE IF NOT EXISTS entities (key TEXT, model TEXT, prompt_version TEXT, name TEXT, type TEXT,
        canonical TEXT);
    CREATE INDEX IF NOT EXISTS ent_key ON entities(key, model, prompt_version);
    CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, model TEXT, prompt_version TEXT,
        submitted_at TEXT, requests INTEGER, status TEXT, collected_at TEXT);
    """)
    return con


def upsert_articles(con, arts):
    con.executemany("INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?)",
                    [(a["key"], a["date"], a["publisher"], a["art_id"], a["page"], a["headline"], a["words"],
                      a["text_hash"]) for a in arts])
    con.commit()


def todo(con, arts, model):
    """Articles without a tag for this exact text, model and prompt version (idempotency)."""
    have = {(k, h) for k, h in con.execute(
        "SELECT key, text_hash FROM tags WHERE model=? AND prompt_version=?", (model, PROMPT_VERSION))}
    return [a for a in arts if (a["key"], a["text_hash"]) not in have]


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


def save_tag(con, a, model, tag, usage, via):
    con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (a["key"], a["text_hash"], model, PROMPT_VERSION, tag["topic"], json.dumps(tag["topics_more"]),
                 tag["kind"], tag["summary"], tag["text_check"], dt.datetime.now().isoformat(timespec="seconds"),
                 usage[0], usage[1], via))
    con.execute("DELETE FROM entities WHERE key=? AND model=? AND prompt_version=?", (a["key"], model, PROMPT_VERSION))
    con.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    [(a["key"], model, PROMPT_VERSION) + e for e in tag["entities"]])


def tool_input(msg):
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


def cost(model, tin, tout, batch=False):
    pin, pout = PRICES[model]
    c = (tin * pin + tout * pout) / 1e6
    return c / 2 if batch else c


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
    arts = load_articles()
    con = db()
    left = todo(con, arts, model)
    by = {}
    for a in arts:
        by[a["publisher"]] = by.get(a["publisher"], 0) + 1
    words = sum(min(len(a["body"].split()), MAX_BODY_WORDS) for a in left)
    f = TOKEN_FACTOR[model]
    tin = int((len(left) * 1100 + words * 1.4) * f)  # ~1,100-token prompt + ~1.4 tokens per word
    tout = int(len(left) * 220 * f)
    print(f"articles (no ET): {len(arts)}  {by}")
    print(f"still to tag with {model} {PROMPT_VERSION}: {len(left)}")
    print(f"estimate: ~{tin/1e6:.1f}M tokens in, ~{tout/1e6:.2f}M out -> "
          f"Batch API ${cost(model, tin, tout, True):.2f} · normal API ${cost(model, tin, tout):.2f}")


def pick_sample(arts, n, seed):
    """Seeded, stratified: the same n/5 articles per paper every time (min 20 words)."""
    rng = random.Random(seed)
    pubs = sorted({a["publisher"] for a in arts})
    per = max(1, n // max(1, len(pubs)))
    out = []
    for p in pubs:
        pool = sorted([a for a in arts if a["publisher"] == p and a["words"] >= 20], key=lambda a: a["key"])
        out += rng.sample(pool, min(per, len(pool)))
    return out


def tag_sync(con, arts, model, cap, workers, label):
    """Normal (non-batch) API calls in parallel. Saves every 50 answers, so Ctrl+C loses little;
    stops sending new work once the measured spend passes the cap."""
    import anthropic
    client = make_client()
    extra = {"on": True}

    def one(a):
        p = params(a, model)
        if extra["on"]:
            try:
                msg = client.messages.create(**p, extra_body=SAMPLING[model])
                return a, msg
            except anthropic.BadRequestError as e:
                extra["on"] = False                  # setting refused by this model: fall back to defaults once
                print(f"  note: request settings {SAMPLING[model]} refused ({str(e)[:150]}); using defaults")
        return a, client.messages.create(**p)

    spent, ok, bad, done = 0.0, 0, 0, 0
    t0 = time.time()
    try:
        for start in range(0, len(arts), 50):
            if spent > cap:
                print(f"  stopped: spend ${spent:.2f} passed the cap ${cap:.2f}. Re-run to continue.")
                break
            chunk = arts[start:start + 50]
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
                    save_tag(con, a, model, tag, usage, "sync")
                    ok += 1
            con.commit()
            if len(arts) > 60:
                mins = (time.time() - t0) / 60
                print(f"  {done}/{len(arts)} done · ${spent:.2f} · {mins:.1f} min")
    except KeyboardInterrupt:
        print("  interrupted: answers so far are saved; re-run to continue.")
    con.commit()
    log(f"{label} model={model} n={len(arts)} ok={ok} bad={bad} cost=${spent:.4f}")
    print(f"tagged {ok}, failed/rejected {bad} (re-run retries them), cost ${spent:.4f}")


def cmd_sample(args, model):
    arts = load_articles()
    sample = pick_sample(arts, args.n, args.seed)
    con = db()
    upsert_articles(con, sample)
    left = todo(con, sample, model)
    print(f"sample: {len(sample)} articles (seed {args.seed}); to tag now: {len(left)} with {model}")
    tag_sync(con, left, model, args.cap, args.workers, "sample")
    write_audit(con, sample, model)


def cmd_run(args, model):
    arts = load_articles()
    con = db()
    left = todo(con, arts, model)
    if not left:
        print("Nothing to tag: every article already has a tag for this model and prompt version.")
        return
    words = sum(min(len(a["body"].split()), MAX_BODY_WORDS) for a in left)
    f = TOKEN_FACTOR[model]
    est = cost(model, int((len(left) * 1100 + words * 1.4) * f), int(len(left) * 220 * f))
    print(f"{len(left)} articles with {model}, normal API; estimated ${est:.2f} (cap ${args.cap:.2f})")
    if est > args.cap:
        sys.exit("Estimate is above --cap; nothing sent. Raise --cap if you mean it.")
    upsert_articles(con, left)
    tag_sync(con, left, model, args.cap, args.workers, "run")


def write_audit(con, sample, model):
    Path("review/tag_audit").mkdir(parents=True, exist_ok=True)
    short = [k for k, v in MODELS.items() if v == model][0]
    path = Path(f"review/tag_audit/audit_{len(sample)}_{short}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for i, a in enumerate(sorted(sample, key=lambda a: a["key"]), 1):
            row = con.execute("SELECT topic, topics_more, kind, summary, text_check FROM tags "
                              "WHERE key=? AND model=? AND prompt_version=?", (a["key"], model, PROMPT_VERSION)).fetchone()
            ents = con.execute("SELECT name, type, canonical FROM entities WHERE key=? AND model=? AND prompt_version=?",
                               (a["key"], model, PROMPT_VERSION)).fetchall()
            f.write(json.dumps({
                "n": f"A{i}", "key": a["key"], "words": a["words"], "headline": a["headline"],
                "body": a["body"][:1500], "topic": row[0] if row else None,
                "topics_more": json.loads(row[1]) if row else None, "kind": row[2] if row else None,
                "summary": row[3] if row else None, "text_check": row[4] if row else None,
                "entities": [{"name": n, "type": t, "canonical": c} for n, t, c in ents],
            }, ensure_ascii=False) + "\n")
    print(f"audit file: {path}  (upload this file to the chat)")


def cmd_submit(args, model):
    arts = load_articles()
    con = db()
    open_b = con.execute("SELECT batch_id FROM batches WHERE status != 'collected'").fetchall()
    if open_b:
        sys.exit(f"A batch is still open: {open_b[0][0]}. Run status / collect first.")
    left = todo(con, arts, model)
    if not left:
        print("Nothing to tag: every article already has a tag for this model and prompt version.")
        return
    words = sum(min(len(a["body"].split()), MAX_BODY_WORDS) for a in left)
    f = TOKEN_FACTOR[model]
    est = cost(model, int((len(left) * 1100 + words * 1.4) * f), int(len(left) * 220 * f), batch=True)
    print(f"{len(left)} articles -> one Batch API job with {model}; estimated ${est:.2f} (cap ${args.cap:.2f})")
    if est > args.cap:
        sys.exit("Estimate is above --cap; nothing sent. Raise --cap if you mean it.")
    upsert_articles(con, left)
    reqs, mapping, used = [], [], set()
    for a in left:
        cid = custom_id(a)
        while cid in used:                                   # custom_id must be unique in a batch
            cid = (cid[:58] + "_" + hashlib.sha1(a["key"].encode()).hexdigest()[:5])
        used.add(cid)
        reqs.append({"custom_id": cid, "params": dict(params(a, model), **SAMPLING[model])})
        mapping.append({"custom_id": cid, "key": a["key"], "text_hash": a["text_hash"]})
    client = make_client()
    batch = client.messages.batches.create(requests=reqs)
    Path("data/batches").mkdir(parents=True, exist_ok=True)
    with open(f"data/batches/{batch.id}.jsonl", "w", encoding="utf-8") as f:
        for m in mapping:
            f.write(json.dumps(m) + "\n")
    con.execute("INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
                (batch.id, model, PROMPT_VERSION, dt.datetime.now().isoformat(timespec="seconds"), len(reqs),
                 "submitted", None))
    con.commit()
    log(f"submit batch={batch.id} model={model} requests={len(reqs)} est=${est:.2f}")
    print(f"submitted batch {batch.id} ({len(reqs)} requests). Check with: status")


def cmd_status(args, model):
    con = db()
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
    con = db()
    rows = con.execute("SELECT batch_id, model FROM batches WHERE status != 'collected'").fetchall()
    if not rows:
        print("No open batch to collect.")
        return
    client = make_client()
    arts = {a["key"]: a for a in load_articles()}
    for bid, bmodel in rows:
        b = client.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            print(f"{bid}: still {b.processing_status}; try again later.")
            continue
        mapping = {}
        with open(f"data/batches/{bid}.jsonl", encoding="utf-8") as f:
            for line in f:
                m = json.loads(line)
                mapping[m["custom_id"]] = m
        ok = bad = stale = 0
        tin = tout = 0
        for entry in client.messages.batches.results(bid):
            m = mapping.get(entry.custom_id)
            if not m or entry.result.type != "succeeded":
                bad += 1
                continue
            msg = entry.result.message
            tin += msg.usage.input_tokens
            tout += msg.usage.output_tokens
            a = arts.get(m["key"])
            if a is None or a["text_hash"] != m["text_hash"]:
                stale += 1                                   # article text changed since submit: retag later
                continue
            tag = validate(tool_input(msg))
            if tag is None:
                bad += 1
                continue
            save_tag(con, a, bmodel, tag, (msg.usage.input_tokens, msg.usage.output_tokens), "batch")
            ok += 1
        con.execute("UPDATE batches SET status='collected', collected_at=? WHERE batch_id=?",
                    (dt.datetime.now().isoformat(timespec="seconds"), bid))
        con.commit()
        spent = cost(bmodel, tin, tout, batch=True)
        log(f"collect batch={bid} ok={ok} bad={bad} stale={stale} tokens_in={tin} tokens_out={tout} cost=${spent:.4f}")
        print(f"{bid}: tagged {ok}, failed/rejected {bad} (re-sent by the next submit), stale {stale}; "
              f"cost ${spent:.2f}")


def cmd_stats(args, model):
    con = db()
    W = "model=? AND prompt_version=?"
    mp = (model, PROMPT_VERSION)
    q = lambda sql: con.execute(sql, mp).fetchall()
    print(f"model {model}, prompt {PROMPT_VERSION}")
    print(f"articles tagged (N): {q(f'SELECT COUNT(*) FROM tags WHERE {W}')[0][0]}")
    print(f"topics used (K): {q(f'SELECT COUNT(DISTINCT topic) FROM tags WHERE {W}')[0][0]} of {len(TOPICS)}")
    print(f"unique entities (E, by canonical name, case-insensitive): "
          f"{q(f'SELECT COUNT(DISTINCT lower(canonical)) FROM entities WHERE {W}')[0][0]}")
    print(f"entity mentions: {q(f'SELECT COUNT(*) FROM entities WHERE {W}')[0][0]}")
    for label, sql in [
        ("by type", f"SELECT type, COUNT(DISTINCT lower(canonical)) FROM entities WHERE {W} GROUP BY type ORDER BY 2 DESC"),
        ("by topic", f"SELECT topic, COUNT(*) FROM tags WHERE {W} GROUP BY topic ORDER BY 2 DESC"),
        ("by kind", f"SELECT kind, COUNT(*) FROM tags WHERE {W} GROUP BY kind ORDER BY 2 DESC"),
        ("text_check", f"SELECT text_check, COUNT(*) FROM tags WHERE {W} GROUP BY text_check ORDER BY 2 DESC"),
        ("entities in 3+ papers", f"SELECT COUNT(*) FROM (SELECT lower(e.canonical) c FROM entities e "
                                  f"JOIN articles a ON a.key=e.key WHERE e.{W.replace(' AND ', ' AND e.')} "
                                  f"GROUP BY c HAVING COUNT(DISTINCT a.publisher) >= 3)"),
        ("top entities", f"SELECT canonical, COUNT(DISTINCT key) FROM entities WHERE {W} GROUP BY lower(canonical) "
                         f"ORDER BY 2 DESC LIMIT 10"),
    ]:
        print(f"{label}: {q(sql)}")
    print(f"cost per run: logs/tag.log (cost= fields)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "sample", "run", "submit", "status", "collect", "stats"])
    ap.add_argument("--model", default="haiku-4.5", choices=sorted(MODELS))
    ap.add_argument("--cap", type=float, default=25.0, help="max estimated dollars for submit")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    model = MODELS[args.model]
    {"plan": cmd_plan, "sample": cmd_sample, "run": cmd_run, "submit": cmd_submit, "status": cmd_status,
     "collect": cmd_collect, "stats": cmd_stats}[args.command](args, model)


if __name__ == "__main__":
    main()
