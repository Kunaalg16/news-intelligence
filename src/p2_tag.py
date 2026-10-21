"""
p2_tag.py -- Phase 2.3 Tag (code v0.3, prompt v1, 21 Oct 2026)

WHAT IT DOES
  Gives every distinct article text of an extraction run a small fixed set of labels from a
  model and stores them in data/news.db, so Phase 3 can filter, group and follow stories
  without re-reading article text.
  Labels per text (prompt v1):
    topic         ONE child of a two-layer list (17 parents, 71 children), or "none";
                  the parent is filled in by lookup (topic_parent), never by the model
    topics_more   0-2 further children
    kind          news | analysis | opinion | interview | brief | data | sponsored | notice | other
    forum         which institution decided or acted: none | Supreme Court | High Court | lower court |
                  tribunal | regulator or agency  (secondary label; the topic stays primary)
    entities      name as printed, type, canonical name
    summary       one sentence, model-written
    text_check    ok | two_stories | fragment | garbled (the model's view of the extraction)
  Plus one label made by a free local rule, not by the model:
    sponsored_cue the printed sponsored cue found in the text ("promotional feature", ...), or ''.

WHY
  Grouping ("everything on RBI this week") and following ("all coverage of Zoho") need a
  controlled vocabulary and named entities, not raw text. Topic list: drafts/2026-10-21_2.3_topic_list_v1.md.

WHAT CHANGED IN v0.3 (prompt v1)
  * Two-layer topic list; the model picks only the child; parent by lookup.
  * Strict tool use ("strict": true): the API constrains the answer to the schema, so a topic,
    kind or forum outside the lists cannot be returned. Limits of the API (no maxItems, no
    maxLength): "at most 2 more topics" and "summary <= 30 words" are asked in the prompt and
    trimmed in code.
  * New field forum; "none" topic for notices, classifieds, ads and sponsored items.
  * Sponsored check: a printed-cue rule in code, stored next to the model's kind (two judges).
  * Rejected answers are saved in the `rejects` table (raw answer + reason), not thrown away.
  * Audit files carry the prompt version in their name (the v0.1 audit files are not overwritten).
  * Needs news.db on schema v2: run `p2_store.py upgrade` (rehearsal), then `upgrade --apply`.
  A new prompt version means every text is tagged again; prompt-v0.1 records stay as they are.

HOW TO RUN (always from ~/news-intel)
  .venv/bin/python src/p2_tag.py plan               import new papers (free, local), count texts, estimate cost; no API call
  .venv/bin/python src/p2_tag.py sample             tag a seeded 60-text sample now (normal API, ~2 min)
                                                    -> review/tag_audit/audit_60_<model>_<prompt>.jsonl
  .venv/bin/python src/p2_tag.py run                tag every untagged text now with the normal API (~2x the batch
                                                    price). Ctrl+C is safe: re-run resumes.
  .venv/bin/python src/p2_tag.py submit             send every untagged text as ONE Batch API job
  .venv/bin/python src/p2_tag.py status             is the batch done yet?
  .venv/bin/python src/p2_tag.py collect            fetch batch results, validate, write news.db
  .venv/bin/python src/p2_tag.py stats              counts by SQL for one run: seats and texts tagged, topics, entities
  .venv/bin/python src/p2_tag.py show-prompt        print the system prompt and tool schema; no API call
  Options: --model haiku-4.5 (default) | haiku-5.5 · --run exam-v1 (default) | pre-freeze · --cap 25 (max dollars)
           --n 60 --seed 19 (sample) · --workers 6 (parallel calls) · --skip-import (tag only what is stored)

SAFETY
  Idempotent: a text is skipped if it already has a record for this model and prompt version.
  Fail-closed: an answer outside the lists is rejected (and saved in `rejects`), never guessed.
  ET is not stored (p2_store). Reads news.db and data/articles/; writes news.db, data/batches/,
  review/tag_audit/ and logs/tag.log.
"""
import argparse, concurrent.futures as cf, datetime as dt, json, os, random, re, sys, time
from pathlib import Path

import p2_store as store

PROMPT_VERSION = "v1"
MODELS = {"haiku-4.5": "claude-haiku-4-5-20251001", "haiku-5.5": "claude-haiku-5-5"}
# Standard (non-batch) prices per million tokens, in / out. Batch API = half.
# Source: official pricing page, checked 15 Oct (drafts/2026-10-15_2.3_tag_design_draft.md).
PRICES = {"claude-haiku-4-5-20251001": (1.00, 5.00), "claude-haiku-5-5": (0.10, 0.50)}   # 5.5: prompts <= 100K tokens
# Per-model request settings (Haiku 5.5 migration guide, checked 19 Oct): Haiku 5.5 rejects temperature other
# than 1 (HTTP 400), so it gets none; effort medium is the setting the model is tuned for.
# Its tokenizer gives ~30% more tokens for the same text (used in estimates).
SAMPLING = {"claude-haiku-4-5-20251001": {"temperature": 0}, "claude-haiku-5-5": {"output_config": {"effort": "medium"}}}
TOKEN_FACTOR = {"claude-haiku-4-5-20251001": 1.0, "claude-haiku-5-5": 1.3}
ESTIMATE_CORRECTION = 1.3      # Tag v0 full run (19 Oct) cost 1.3x the estimate; applied to every estimate
MAX_BODY_WORDS = 1200

# ---------- the labels (topic list v1, drafts/2026-10-21_2.3_topic_list_v1.md) ----------
TAXONOMY = [
    ("Politics & Government", ["Elections & voter rolls", "Parties & leaders", "Legislatures & appointments",
                               "Protests & movements", "Government schemes & welfare", "Judiciary"]),
    ("Economy", ["Growth, jobs & data", "Inflation & monetary policy", "Taxes & budget", "Trade & tariffs",
                 "Industry & manufacturing policy", "Agriculture & food"]),
    ("Markets", ["Stocks & IPOs", "Currency & bonds", "Commodities & gold", "Mutual funds & trading"]),
    ("Companies & Business", ["Results & outlook", "Deals & investments", "Startups & funding",
                              "Products & launches", "People, governance & disputes"]),
    ("Banking & Finance", ["Banks & lenders", "Insurance", "Payments & fintech", "Personal finance"]),
    ("Technology", ["AI", "Chips & electronics", "Telecom & digital policy", "Consumer tech & apps"]),
    ("Energy", ["Oil & gas", "Power & coal", "Renewables"]),
    ("Environment & Climate", ["Climate change", "Pollution", "Wildlife & conservation",
                               "Weather & natural disasters"]),
    ("Infrastructure & Transport", ["Aviation", "Rail & metro", "Road safety & roads", "Shipping & logistics",
                                    "Urban infrastructure & traffic"]),
    ("Crime & Public Safety", ["Violent crime", "Fraud, corruption & cybercrime", "Policing",
                               "Accidents & tragedies"]),
    ("Defence & National Security", ["Armed forces", "Terrorism & insurgency", "Espionage & borders"]),
    ("World", ["India's foreign relations", "United States", "Wars & conflicts", "Other countries",
               "Visas & diaspora"]),
    ("Health", ["Public health & policy", "Hospitals & healthcare", "Pharma & medicines", "Fitness & wellness"]),
    ("Education", ["Schools", "Higher education", "Exams & admissions"]),
    ("Science", ["Space", "Research & discoveries"]),
    ("Sports", ["Cricket", "Football & hockey", "Other sports", "Sports administration"]),
    ("Culture & Lifestyle", ["Film, TV & streaming", "Books & arts", "Heritage & religion", "Travel & food",
                             "Life & society"]),
]
SEP = " › "
PARENT_OF = {child: parent for parent, children in TAXONOMY for child in children}
TOPIC_ENUM = [parent + SEP + child for parent, children in TAXONOMY for child in children]   # what the model sees
NO_TOPIC = "none"
KINDS = ["news", "analysis", "opinion", "interview", "brief", "data", "sponsored", "notice", "other"]
FORUMS = ["none", "Supreme Court", "High Court", "lower court", "tribunal", "regulator or agency"]
ENTITY_TYPES = ["company", "person", "organisation", "place"]
TEXT_CHECKS = ["ok", "two_stories", "fragment", "garbled"]
assert len(PARENT_OF) == sum(len(c) for _, c in TAXONOMY), "child names must be unique across parents"

# Printed cues that mark paid content. A free local rule, kept separate from the model's `kind`
# so the two can be compared ("two independent judges"). Phrases, not single words: "sponsored"
# alone appears in news ("state-sponsored").
SPONSORED_CUES = ["promotional feature", "advertorial", "in association with", "marketing initiative",
                  "consumer connect initiative", "impact feature", "brand connect", "partner content",
                  "sponsored content", "sponsored feature"]
CUE_RE = re.compile("|".join(re.escape(c) for c in SPONSORED_CUES), re.IGNORECASE)

TOPIC_LINES = "\n".join(f"  {parent}: " + "; ".join(children) for parent, children in TAXONOMY)

SYSTEM = f"""You label articles from Indian national newspapers for a personal news archive.
Read the WHOLE article, then call the tool tag_article exactly once.
Judge from the body text. Headlines are sometimes a section label ("QUICKLY.", "THE GIST"), a pull quote
or a fragment; when the headline is empty or says little, the body decides.

topic: the ONE subject the article is about, as "Parent › Child" from this list (pick the child; the parent comes with it):
{TOPIC_LINES}
  Use "none" only for kind notice or sponsored (classifieds, matrimonial ads, display ads, tenders,
  public notices, obituaries, paid content).
topics_more: 0 to 2 further children from the same list, only if clearly central (never repeat topic). Usually 0 or 1.

Rules for overlapping subjects (apply them; they override your own preference):
- Courts, tribunals and regulators are institutions, not subjects. Topic = what the ruling or action is about;
  the institution goes in forum. Example: "Supreme Court upholds forest rule" -> Environment & Climate › Wildlife &
  conservation, forum Supreme Court. "Sebi bars firm from markets" -> Markets › Stocks & IPOs, forum regulator or agency.
  Use Politics & Government › Judiciary only when the courts themselves are the subject (judges' appointments,
  recusals, case backlog, the Chief Justice's administration).
- Accidents: a report of an accident or tragedy (road crash, building collapse, boat capsizing, stampede, fire)
  -> Crime & Public Safety › Accidents & tragedies. Infrastructure & Transport › Road safety & roads only when the
  article is about policy: road design, safety rules, enforcement drives. Drunk driving and hit-and-run -> Crime & Public
  Safety › Violent crime.
- RBI: a rate or monetary-policy decision -> Economy › Inflation & monetary policy; its bond, forex or liquidity
  operations -> Markets › Currency & bonds; rules and supervision for banks -> Banking & Finance › Banks & lenders.
- Military, navy, air force, defence purchases, DRDO, missile tests -> Defence & National Security, even when another
  country is involved. Diplomacy and summits -> World › India's foreign relations.
- Weather, rain, monsoon, cyclones, floods, heatwaves -> Environment & Climate › Weather & natural disasters.
- Personal-finance advice (deposits, pensions, insurance cover, which fund to buy) -> Banking & Finance › Personal finance.
- Fund-raising by a private equity or venture fund -> Companies & Business › Startups & funding.
- Prizes go to the subject of the prize (a physics Nobel -> Science › Research & discoveries).

kind: news (straight report) | analysis (feature, explainer) | opinion (editorial, column, letter) |
  interview | brief (under ~80 words, one item) | data (scores, results, market wrap, tables, listings) |
  sponsored (paid content: advertorial, "marketing initiative", "promotional feature", "in association with",
  "consumer connect initiative", supplement pages written for an advertiser) |
  notice (obituary, tender, public notice, classified or matrimonial ad, display ad) | other.
  Every sponsored item must be caught: if printed cues or the tone show the text was paid for, use sponsored.
forum: the institution that decided or acted in the article: Supreme Court | High Court | lower court (district,
  sessions, magistrate, family courts) | tribunal (NCLT, NCLAT, NGT, DGTR, CAT, consumer commissions) |
  regulator or agency (SEBI, RBI acting as regulator, CCI, TRAI, DGCA, FSSAI, Election Commission, ED, CBI, NIA, police
  investigations led by an agency) | none.
entities: the important named companies, people, organisations (incl. government bodies, regulators) and places
  that the article is about. name = as printed; canonical = the full standard name
  (e.g. "RBI" -> "Reserve Bank of India", "Modi" -> "Narendra Modi"). Do not list the newspaper itself or reporters.
summary: one neutral sentence, at most 30 words, in your own words.
text_check: ok | two_stories (text clearly mixes two unrelated stories) | fragment (starts or ends mid-story,
  or too little text to judge) | garbled (unreadable text)."""

TOOL = {
    "name": "tag_article",
    "description": "Record the labels for one article.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": TOPIC_ENUM + [NO_TOPIC]},
            "topics_more": {"type": "array", "items": {"type": "string", "enum": TOPIC_ENUM}},
            "kind": {"type": "string", "enum": KINDS},
            "forum": {"type": "string", "enum": FORUMS},
            "entities": {"type": "array", "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ENTITY_TYPES},
                               "canonical": {"type": "string"}},
                "required": ["name", "type", "canonical"],
                "additionalProperties": False}},
            "summary": {"type": "string"},
            "text_check": {"type": "string", "enum": TEXT_CHECKS},
        },
        # all required: no optional parameters (API limit 24) and a fixed answer order
        "required": ["topic", "topics_more", "kind", "forum", "entities", "summary", "text_check"],
        "additionalProperties": False,
    },
}


# ---------- texts ----------
def open_run(args, write=True):
    """Open news.db, bring the run up to date with its JSON folder, return the connection.
    Writing prompt-v1 records needs schema v2."""
    con = store.open_v2() if write else store.open_v1()
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


def sponsored_cue(a):
    """Free local rule: the first printed sponsored cue in headline, deck or body ('' = none)."""
    m = CUE_RE.search(" ".join([a.get("headline") or "", a.get("deck") or "", a.get("body") or ""]))
    return m.group(0).lower() if m else ""


def params(a, model):
    return {"model": model, "max_tokens": 1500, "system": SYSTEM, "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "tag_article"},
            "messages": [{"role": "user", "content": user_text(a)}]}


def todo(con, texts, model):
    """Texts without a record for this model and prompt version (idempotency)."""
    have = {h for (h,) in con.execute("SELECT text_hash FROM tags WHERE model=? AND prompt_version=?",
                                      (model, PROMPT_VERSION))}
    return [a for a in texts if a["text_hash"] not in have]


def child_of(path):
    """'Parent › Child' -> 'Child' (None if not on the list)."""
    if path not in TOPIC_ENUM:
        return None
    return path.split(SEP, 1)[1]


def validate(inp):
    """Fail-closed. Returns (tag, None) or (None, reason). Strict tool use should make every list value
    valid; this check stays as the second judge (and catches truncated or missing answers)."""
    if not isinstance(inp, dict):
        return None, "no tool answer"
    topic_path = inp.get("topic")
    if topic_path != NO_TOPIC and child_of(topic_path) is None:
        return None, f"topic not on the list: {topic_path!r}"
    if inp.get("kind") not in KINDS:
        return None, f"kind not on the list: {inp.get('kind')!r}"
    if inp.get("forum") not in FORUMS:
        return None, f"forum not on the list: {inp.get('forum')!r}"
    topic = None if topic_path == NO_TOPIC else child_of(topic_path)
    more = []
    for p in inp.get("topics_more") or []:
        c = child_of(p)
        if c and c != topic and c not in more:
            more.append(c)
    more = more[:2]                                       # the API cannot enforce maxItems; trimmed here
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
    return {"topic": topic, "topic_parent": PARENT_OF.get(topic), "topics_more": more, "kind": inp["kind"],
            "forum": None if inp["forum"] == "none" else inp["forum"], "entities": ents,
            "summary": str(inp.get("summary") or "").strip()[:400], "text_check": tc}, None


def save_tag(con, a, model, tag, usage, via):
    store.insert_tag(con, {
        "text_hash": a["text_hash"], "model": model, "prompt_version": PROMPT_VERSION, "topic": tag["topic"],
        "topics_more": json.dumps(tag["topics_more"]), "kind": tag["kind"], "summary": tag["summary"],
        "text_check": tag["text_check"], "tagged_at": dt.datetime.now().isoformat(timespec="seconds"),
        "in_tokens": usage[0], "out_tokens": usage[1], "via": via, "topic_parent": tag["topic_parent"],
        "forum": tag["forum"], "sponsored_cue": sponsored_cue(a)})
    h = a["text_hash"]
    con.execute("DELETE FROM entities WHERE text_hash=? AND model=? AND prompt_version=?", (h, model, PROMPT_VERSION))
    con.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?)",
                    [(h, model, PROMPT_VERSION) + e for e in tag["entities"]])


def save_reject(con, h, model, reason, stop_reason, raw, via):
    con.execute("INSERT INTO rejects VALUES (?,?,?,?,?,?,?,?)",
                (h, model, PROMPT_VERSION, dt.datetime.now().isoformat(timespec="seconds"), reason,
                 stop_reason, json.dumps(raw, ensure_ascii=False, default=str)[:20000], via))


def tool_input(msg):
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


def cost(model, tin, tout, batch=False):
    pin, pout = PRICES[model]
    c = (tin * pin + tout * pout) / 1e6
    return c / 2 if batch else c


def prompt_tokens(model):
    """Rough size of the fixed part of every request (system prompt + tool schema): ~4 characters per token."""
    return int(len(SYSTEM + json.dumps(TOOL)) / 4 * TOKEN_FACTOR[model])


def estimate(texts, model, batch=False):
    words = sum(min(len(a["body"].split()), MAX_BODY_WORDS) for a in texts)
    f = TOKEN_FACTOR[model]
    tin = int((len(texts) * prompt_tokens(model) + words * 1.4 * f) * ESTIMATE_CORRECTION)
    tout = int(len(texts) * 240 * f * ESTIMATE_CORRECTION)
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
    print(f"prompt {PROMPT_VERSION}: {len(TAXONOMY)} parents, {len(TOPIC_ENUM)} children; fixed part ~{prompt_tokens(model)} "
          f"tokens per request")
    print(f"already tagged with {model} {PROMPT_VERSION}: {len(texts) - len(left)} · still to tag: {len(left)}")
    print(f"estimate (x{ESTIMATE_CORRECTION} correction from Tag v0): ~{tin/1e6:.1f}M tokens in, ~{tout/1e6:.2f}M out -> "
          f"Batch API ${estimate(left, model, True)[2]:.2f} · normal API ${c_norm:.2f}")


def cmd_show_prompt(args, model):
    print(SYSTEM)
    print("\n--- tool schema ---")
    print(json.dumps(TOOL, indent=1, ensure_ascii=False))
    print(f"\n~{prompt_tokens(model)} tokens for {model} (estimate)")


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
                if "strict" in str(e).lower() or "schema" in str(e).lower():
                    raise                            # a schema refusal must stop the run, not be retried without it
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
                futs = {ex.submit(one, a): a for a in chunk}
                for fut in cf.as_completed(futs):
                    done += 1
                    a = futs[fut]
                    try:
                        _, msg = fut.result()
                        inp, usage = tool_input(msg), (msg.usage.input_tokens, msg.usage.output_tokens)
                    except anthropic.BadRequestError as e:
                        con.execute("COMMIT")
                        sys.exit(f"  request refused by the API, stopping: {str(e)[:300]}")
                    except Exception as e:
                        bad += 1
                        save_reject(con, a["text_hash"], model, "call failed", None, str(e)[:2000], "sync")
                        print(f"  call failed: {str(e)[:200]}")
                        continue
                    spent += cost(model, *usage)
                    tag, why = validate(inp)
                    if tag is None:
                        bad += 1
                        save_reject(con, a["text_hash"], model, why, getattr(msg, "stop_reason", None), inp, "sync")
                        print(f"  rejected ({why}; stop_reason {getattr(msg, 'stop_reason', '?')}): {a['key']}")
                        continue
                    save_tag(con, a, model, tag, usage, "sync")
                    ok += 1
            con.execute("COMMIT")
            if len(texts) > 60:
                print(f"  {done}/{len(texts)} done · ${spent:.2f} · {(time.time() - t0) / 60:.1f} min")
    except KeyboardInterrupt:
        print("  interrupted: answers so far are saved; re-run to continue.")
    if con.in_transaction:
        con.execute("COMMIT")
    log(f"{label} model={model} prompt={PROMPT_VERSION} n={len(texts)} ok={ok} bad={bad} cost=${spent:.4f}")
    print(f"tagged {ok}, failed/rejected {bad} (saved in table rejects; re-run retries them), cost ${spent:.4f}")


def cmd_sample(args, model):
    con = open_run(args)
    sample = pick_sample(load_texts(con, args.run), args.n, args.seed)
    left = todo(con, sample, model)
    print(f"sample: {len(sample)} texts from run {args.run} (seed {args.seed}); to tag now: {len(left)} with "
          f"{model} {PROMPT_VERSION}")
    tag_sync(con, left, model, args.cap, args.workers, "sample")
    write_audit(con, sample, model)


def cmd_run(args, model):
    con = open_run(args)
    left = todo(con, load_texts(con, args.run), model)
    if not left:
        print(f"Nothing to tag: every text in run {args.run} already has a record for this model and prompt version.")
        return
    est = estimate(left, model)[2]
    print(f"{len(left)} texts with {model} {PROMPT_VERSION}, normal API; estimated ${est:.2f} (cap ${args.cap:.2f})")
    if est > args.cap:
        sys.exit("Estimate is above --cap; nothing sent. Raise --cap if you mean it.")
    tag_sync(con, left, model, args.cap, args.workers, f"run[{args.run}]")


def write_audit(con, sample, model):
    Path("review/tag_audit").mkdir(parents=True, exist_ok=True)
    short = [k for k, v in MODELS.items() if v == model][0]
    path = Path(f"review/tag_audit/audit_{len(sample)}_{short}_{PROMPT_VERSION}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for i, a in enumerate(sorted(sample, key=lambda a: a["key"]), 1):
            row = con.execute("SELECT topic, topic_parent, topics_more, kind, forum, sponsored_cue, summary, "
                              "text_check FROM tags WHERE text_hash=? AND model=? AND prompt_version=?",
                              (a["text_hash"], model, PROMPT_VERSION)).fetchone()
            ents = con.execute("SELECT name, type, canonical FROM entities WHERE text_hash=? AND model=? "
                               "AND prompt_version=?", (a["text_hash"], model, PROMPT_VERSION)).fetchall()
            r = dict(zip(["topic", "topic_parent", "topics_more", "kind", "forum", "sponsored_cue", "summary",
                          "text_check"], row)) if row else {}
            f.write(json.dumps({
                "n": f"A{i}", "key": a["key"], "text_hash": a["text_hash"], "words": a["words"],
                "headline": a["headline"], "body": a["body"][:1500],
                "topic": r.get("topic"), "topic_parent": r.get("topic_parent"),
                "topics_more": json.loads(r["topics_more"]) if row else None, "kind": r.get("kind"),
                "forum": r.get("forum"), "sponsored_cue": r.get("sponsored_cue"), "summary": r.get("summary"),
                "text_check": r.get("text_check"),
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
    print(f"{len(left)} texts -> one Batch API job with {model} {PROMPT_VERSION}; estimated ${est:.2f} "
          f"(cap ${args.cap:.2f})")
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
    log(f"submit batch={batch.id} model={model} prompt={PROMPT_VERSION} run={args.run} requests={len(reqs)} "
        f"est=${est:.2f}")
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
    con = store.open_v2()
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
        texts = {}
        for h, head, body in con.execute("SELECT text_hash, headline, body FROM texts"):
            texts[h] = {"text_hash": h, "headline": head, "body": body}
        decks = dict(con.execute("SELECT text_hash, MIN(deck) FROM placements GROUP BY text_hash"))
        ok = bad = 0
        tin = tout = 0
        con.execute("BEGIN")
        for entry in client.messages.batches.results(bid):
            h = mapping.get(entry.custom_id)
            if not h:
                bad += 1
                continue
            if entry.result.type != "succeeded":
                bad += 1
                save_reject(con, h, bmodel, f"batch result {entry.result.type}", None,
                            getattr(entry.result, "error", None), "batch")
                continue
            msg = entry.result.message
            tin += msg.usage.input_tokens
            tout += msg.usage.output_tokens
            inp = tool_input(msg)
            tag, why = validate(inp)
            if tag is None:
                bad += 1
                save_reject(con, h, bmodel, why, getattr(msg, "stop_reason", None), inp, "batch")
                continue
            a = dict(texts.get(h, {"text_hash": h, "headline": "", "body": ""}), deck=decks.get(h) or "")
            save_tag(con, a, bmodel, tag, (msg.usage.input_tokens, msg.usage.output_tokens), "batch")
            ok += 1
        con.execute("UPDATE batches SET status='collected', collected_at=? WHERE batch_id=?",
                    (dt.datetime.now().isoformat(timespec="seconds"), bid))
        con.execute("COMMIT")
        spent = cost(bmodel, tin, tout, batch=True)
        log(f"collect batch={bid} prompt={bpv} ok={ok} bad={bad} tokens_in={tin} tokens_out={tout} cost=${spent:.4f}")
        print(f"{bid}: tagged {ok}, failed/rejected {bad} (saved in table rejects; re-sent by the next submit); "
              f"cost ${spent:.2f}")


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
        ("children used", f"SELECT COUNT(DISTINCT t.topic) {J}"),
        ("by parent", f"SELECT t.topic_parent, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("by child (top 25)", f"SELECT t.topic, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC LIMIT 25"),
        ("by kind", f"SELECT t.kind, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("by forum", f"SELECT t.forum, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("sponsored: cue rule vs model", f"SELECT t.sponsored_cue != '', t.kind = 'sponsored', COUNT(*) {J} "
                                         f"GROUP BY 1, 2 ORDER BY 1, 2"),
        ("text_check", f"SELECT t.text_check, COUNT(*) {J} GROUP BY 1 ORDER BY 2 DESC"),
        ("by type", f"SELECT e.type, COUNT(DISTINCT lower(e.canonical)) {E} GROUP BY 1 ORDER BY 2 DESC"),
        ("top entities (seats)", f"SELECT e.canonical, COUNT(DISTINCT pl.date||pl.publisher||pl.art_id) {E} "
                                 f"GROUP BY lower(e.canonical) ORDER BY 2 DESC LIMIT 10"),
    ]:
        print(f"{label}: {con.execute(sql, p).fetchall()}")
    if "rejects" in store.tables(con):
        rj = con.execute("SELECT reason, COUNT(*) FROM rejects WHERE model=? AND prompt_version=? GROUP BY 1 "
                         "ORDER BY 2 DESC", (model, PROMPT_VERSION)).fetchall()
        print(f"rejected answers kept: {rj}")
    print("cost per run: logs/tag.log (cost= fields)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "sample", "run", "submit", "status", "collect", "stats", "show-prompt"])
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
     "collect": cmd_collect, "stats": cmd_stats, "show-prompt": cmd_show_prompt}[args.command](args, model)


if __name__ == "__main__":
    main()
