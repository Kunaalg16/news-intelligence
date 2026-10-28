"""p2_dev_judgetest.py · 2.4 dev tool · fresh agreement test for the stage-1 judge (v0.2, 28 Oct 2026)

WHAT   Builds the blind labelling sheet for the agreement test of the "same story" judge
       (drafts/2026-10-28_B2.4_judge_definition_and_test.md, pre-registered 28 Oct).
       `sample` draws 200 cross-paper middle-band pairs from stage 1 (intel.db, grouping g0.1) and writes
         data/pairs/pairs_<name>.jsonl     the answer sheet (hashes, date, score) -- never shown while labelling
         review/pairs/sheet_<name>.html    the labelling page: same layout as the 2.4 pair sheets, new ladder:
                                           1 Same event · 2 Same story, different event · 3 Same subject only ·
                                           4 Unrelated · 5 Can't tell; optional anchor box; the approved
                                           definition (v1.0) in a fold-out. Labels download as labels_<name>.json.
WHY    S6 (28 Oct) made the story the unit and a four-rung ladder the judge's output; the old 212 pairs
       cannot test it (in-sample, discrepant analysis). Gate: kappa >= 0.70 on the merge decision.
POOL   (pre-registered, before any draw)  pairs table, version g0.1, band 'middle', same_paper 0;
       NOT on the worked-example dates (17-18 Sep, 7, 8, 11, 12, 15, 22 Oct)  -- deviation from S6's
       "new dates", Kunaal 28 Oct;  NOT containing any text from pairs_v1.jsonl / pairs_v1x.jsonl;
       NOT containing a text flagged `fragment` (v1.3 text_check) or with < 50 body words;
       NOT containing junk (kind notice, or sponsored with no topic -- as the 2.4 pair sheets).
       Draw (v0.2, amended 28 Oct after seeing only per-date COUNTS, no article or label -- pre-result
       amendment, Kunaal): the simple random draw put 156 of 200 pairs on 3 dense dates (9, 13, 14 Oct).
       Now SPREAD: pairs are shared out across dates in proportion to each date's pool, but no date gets
       more than --per-date (default 25); a date with fewer eligible pairs gives all it has, the rest goes to
       the others. And no article appears in more than --per-text pairs (default 3). Seeded shuffle of the
       sorted pool, then pairs taken in that order under both limits. Left/right and order randomised; ids J001...
HOW    cd ~/news-intel && .venv/bin/python src/p2_dev_judgetest.py sample --name j1
       options: --n 200 --seed 2028 --per-date 25 --per-text 3 --grouping g0.1 --dry (counts only) --force
       Reads intel.db, news.db (read-only). Writes only the two files above. Standard library only.
"""
import argparse, html, json, random, re, sqlite3, sys
from pathlib import Path

NEWS_DB, INTEL_DB = Path("data") / "news.db", Path("data") / "intel.db"
PAIRS_DIR, SHEET_DIR = Path("data") / "pairs", Path("review") / "pairs"
EXAMPLE_DATES = {"2026-09-17", "2026-09-18", "2026-10-07", "2026-10-08",
                 "2026-10-11", "2026-10-12", "2026-10-15", "2026-10-22"}
OLD_SETS = ("v1", "v1x")
MIN_WORDS = 50
EXCERPT_WORDS = 140
DEFINITION_VERSION = "v1.0"
DEFINITION = '**Your task.** You will see two newspaper articles published on the same day, in different papers. Decide how they relate, using exactly one of four labels. Then name the anchor (below) if your label is one of the first two.\n\n**The idea behind the labels.** News is organised around *happenings*: specific, dated occurrences such as a decision, an announcement, a ruling, a release of figures, an incident, an arrest, a protest action, a speech, a match, a vote or a deal. A **story** is one such happening together with the further happenings directly tied to it: reactions to it, consequences of it, other actions taken in the same episode on the same day. A **subject** is a broader theme (a policy, a programme, a long process, a condition, an industry, an organisation, a person) under which many separate stories fall.\n\n**The four labels, from closest to farthest:**\n1. **same event** — both articles report the same specific happening. Different angles, quotes or details of that one happening still count: the victim\'s account and the police response to the same incident; two different statements made by the same person at the same press conference; two parts of the same deal announced together.\n2. **same story, different event** — the articles report different happenings, but both stem from one specific, dated happening you can name (the anchor). Example: on a day of protests in Delhi, one article reports the metro stations closed to stop protesters and the other reports police detaining journalists at the protest site; both stem from that day\'s protest action.\n3. **same subject only** — the articles share a theme, but you cannot name one specific dated happening that both stem from. Example: one article on a chip maker scaling up its assembly plant in India, another on the Prime Minister\'s speech about chips and private investment: same subject (semiconductors in India), different stories.\n4. **unrelated** — no shared subject.\n\n**The anchor rule (decides between 2 and 3).** For label 1 or 2 you must name the anchor in a few words, with its date if the articles give it (for example "RBI policy announcement, <date>"; "Delhi protests, <date>"). The anchor must be a **specific dated occurrence**. A policy, a programme, a long-running process, a condition, an organisation or a person is **not** an anchor; if that is all the two articles share, the label is **same subject only**. The anchor may have happened before the articles were printed: reactions on Friday to Wednesday\'s decision share that decision as their anchor. If you cannot name an anchor both articles stem from, do not use label 1 or 2.\n\n**Worked examples (from real pairs):**\n- *Same event.* A report that the US clinched a deal to buy Russian fuel while criticising India for doing the same, and a report that the US lifted sanctions on Russian diesel sales: two parts of one announced deal. → same event; anchor: the US deal on Russian diesel.\n- *Same event.* A cricket coach\'s praise for a bowler\'s spell, and the same coach saying his batters must learn to defend: both said at the same post-match press conference. → same event; anchor: the post-match press conference.\n- *Same story, different event.* On the same day of the Delhi protests, one article on protest sites at Jantar Mantar and police action there, another on checkpoints at Delhi\'s borders stopping people from entering. → same story; anchor: the Delhi protests that day.\n- *Same story, different event.* The news that a payment charge (MDR) will apply to UPI, a petrol dealers\' association reacting to it, an analysis of what it means for bank earnings, and an editorial on it, all printed the same day. → each pair is same story; anchor: the MDR-on-UPI announcement.\n- *Same subject only.* Two reports on two different cricket matches. → no shared happening.\n- *Same subject only.* A protest in Delhi and a protest in Mumbai on the same day, both against the same policy: they share a movement (a long process), not one dated happening. → same subject only, **unless** one is a reaction to a specific happening in the other.\n- *Same subject only.* The Supreme Court on an anomaly in Form 6 filings, and an article on the effects of the electoral-roll revision (SIR). → same subject (the SIR), **unless** both articles report or react to one specific ruling or hearing that day, in which case same story.\n- *It depends on the anchor.* RBI revising its growth and inflation forecasts, and consumption stocks falling after a repo-rate hike: same story **if** both stem from the same policy announcement; same subject only if they are about different occasions.\n\n**Output:** `label` (one of the four) · `anchor` (a few words with a date if known; empty for labels 3 and 4) · `reason` (one sentence).'


def ro(path):
    if not path.exists():
        sys.exit(f"{path} not found. Run from ~/news-intel.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def old_texts():
    hs = set()
    for s in OLD_SETS:
        p = PAIRS_DIR / f"pairs_{s}.jsonl"
        if not p.exists():
            sys.exit(f"{p} not found (needed to exclude the old pairs' texts).")
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            r = json.loads(line)
            hs.update((r["a"], r["b"]))
    return hs


def build_pool(args):
    news, intel = ro(NEWS_DB), ro(INTEL_DB)
    rows = intel.execute("SELECT date, a, b, score FROM pairs WHERE version=? AND band='middle' AND same_paper=0",
                         (args.grouping,)).fetchall()
    if not rows:
        have = intel.execute("SELECT version, band, COUNT(*) FROM pairs GROUP BY 1, 2").fetchall()
        sys.exit(f"no middle-band pairs for {args.grouping}. Present: {have}")
    seat = {}
    for h, d, pub, page, deck, head, body in news.execute(
            "SELECT p.text_hash, p.date, p.publisher, p.page, p.deck, t.headline, t.body FROM placements p "
            "JOIN texts t ON t.text_hash = p.text_hash WHERE p.run_id = ? ORDER BY p.text_hash, p.date, p.publisher, p.art_id",
            (args.run,)):
        if h not in seat:
            seat[h] = {"date": d, "paper": pub, "page": page, "deck": deck or "", "head": head or "", "body": body or ""}
    tag = {h: (topic, kind, check) for h, topic, kind, check in news.execute(
        "SELECT text_hash, topic, kind, text_check FROM tags WHERE model=? AND prompt_version=?",
        (args.tag_model, args.tag_prompt))}
    old = old_texts()

    def why_out(h):
        if h not in seat or h not in tag:
            return "no seat or tag"
        topic, kind, check = tag[h]
        if h in old:
            return "old 212-pair text"
        if check == "fragment":
            return "fragment"
        if len(seat[h]["body"].split()) < MIN_WORDS:
            return f"< {MIN_WORDS} words"
        if kind == "notice" or (kind == "sponsored" and topic is None):
            return "junk"
        return None

    counts = {"middle cross-paper pairs": len(rows)}
    pool = []
    for d, a, b, sc in rows:
        if d in EXAMPLE_DATES:
            counts["example date"] = counts.get("example date", 0) + 1
            continue
        r = why_out(a) or why_out(b)
        if r:
            counts[r] = counts.get(r, 0) + 1
            continue
        pool.append((d, a, b, sc))
    pool.sort()
    counts["pool"] = len(pool)
    counts["dates in pool"] = len({p[0] for p in pool})
    return pool, seat, counts


def spread_draw(pool, n, per_date, per_text, rng):
    """Proportional-to-pool share per date, capped at per_date; at most per_text pairs per article.
    Deterministic for a given seed: one seeded shuffle, then pairs taken in that order."""
    order = pool[:]
    rng.shuffle(order)
    size = {}
    for d, *_ in pool:
        size[d] = size.get(d, 0) + 1
    quota, got, uses, taken = {d: 0 for d in size}, {d: 0 for d in size}, {}, []
    used, stuck = set(), set()
    while len(taken) < n:
        open_d = [d for d in size if quota[d] < min(per_date, size[d]) and d not in stuck]
        if not open_d:
            break
        # give the next slot to the date with the largest pool per slot already given (proportional share)
        d = max(sorted(open_d), key=lambda x: size[x] / (quota[x] + 1))
        quota[d] += 1
        for k, (dd, a, b, sc) in enumerate(order):
            if dd != d or k in used or uses.get(a, 0) >= per_text or uses.get(b, 0) >= per_text:
                continue
            used.add(k)
            uses[a] = uses.get(a, 0) + 1
            uses[b] = uses.get(b, 0) + 1
            taken.append((dd, a, b, sc))
            got[d] += 1
            break
        else:
            quota[d] -= 1
            stuck.add(d)        # no pair left on this date under the per-article limit
    return taken


def card(s):
    words = s["body"].split()
    ex = " ".join(words[:EXCERPT_WORDS]) + (" …" if len(words) > EXCERPT_WORDS else "")
    return {"paper": s["paper"], "date": s["date"], "page": s["page"], "head": s["head"], "deck": s["deck"],
            "excerpt": ex, "words": len(words), "rest": " ".join(words[EXCERPT_WORDS:])}


def md_html(md):
    out = []
    for para in md.strip().split("\n\n"):
        t = html.escape(para.strip())
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        lines = t.split("\n")
        if all(l.lstrip().startswith(("- ", "1.", "2.", "3.", "4.")) for l in lines[1:]) and len(lines) > 1:
            out.append(f"<p>{lines[0]}</p><ul>" + "".join(f"<li>{re.sub(r'^(- |[0-9][.] )', '', l.strip())}</li>" for l in lines[1:]) + "</ul>")
        else:
            out.append(f"<p>{'<br>'.join(lines)}</p>")
    return "".join(out)


def cmd_sample(args):
    out = PAIRS_DIR / f"pairs_{args.name}.jsonl"
    if out.exists() and not (args.force or args.dry):
        sys.exit(f"{out} exists. A new test set needs a new --name (or --force, before any labelling).")
    pool, seat, counts = build_pool(args)
    print("pool: " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    if len(pool) < args.n:
        sys.exit(f"STOP: pool {len(pool)} < {args.n} pairs wanted; nothing written.")
    rng = random.Random(args.seed)
    pick = spread_draw(pool, args.n, args.per_date, args.per_text, rng)
    if len(pick) < args.n:
        sys.exit(f"STOP: only {len(pick)} pairs can be drawn under --per-date {args.per_date} "
                 f"--per-text {args.per_text}; nothing written.")
    rng.shuffle(pick)
    pairs = []
    for i, (d, a, b, sc) in enumerate(pick, 1):
        if rng.random() < 0.5:
            a, b = b, a
        pairs.append({"pair": f"J{i:03d}", "date": d, "a": a, "b": b, "score": round(sc, 4)})
    per_date = {}
    for p in pairs:
        per_date[p["date"]] = per_date.get(p["date"], 0) + 1
    tx = {}
    for p in pairs:
        for h in (p["a"], p["b"]):
            tx[h] = tx.get(h, 0) + 1
    print(f"max pairs per article {max(tx.values())} · articles {len(tx)}")
    print(f"drawn {len(pairs)} (seed {args.seed}) from {len(per_date)} dates: "
          + " ".join(f"{d[5:]}:{n}" for d, n in sorted(per_date.items())))
    if args.dry:
        print("dry run: nothing written")
        return
    meta = {"meta": {"test": "stage-1 judge agreement", "definition": DEFINITION_VERSION, "grouping": args.grouping,
                     "run": args.run, "tags": f"{args.tag_model} {args.tag_prompt}", "seed": args.seed, "n": args.n,
                     "per_date": args.per_date, "per_text": args.per_text, "draw": "spread v0.2",
                     "counts": counts, "excluded_dates": sorted(EXAMPLE_DATES), "excluded_sets": list(OLD_SETS),
                     "min_words": MIN_WORDS, "design": "drafts/2026-10-28_B2.4_judge_definition_and_test.md"}}
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    print(f"pairs: {out}")
    data = [{"pair": p["pair"], "a": card(seat[p["a"]]), "b": card(seat[p["b"]])} for p in pairs]
    page = SHEET.replace("__TITLE__", html.escape(f"Judge test · {args.name}")).replace("__NAME__", args.name) \
                .replace("__DEF__", md_html(DEFINITION)).replace("__DEFV__", DEFINITION_VERSION) \
                .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    sp = SHEET_DIR / f"sheet_{args.name}.html"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(page, encoding="utf-8")
    print(f"sheet: {sp}  ({len(data)} pairs) -- open it in your browser")


SHEET = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#deded8;--acc:#2f5d8a;--sel:#e3ecf5}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#a3a29c;--card:#1f1f1d;--line:#3a3a36;--acc:#8fb4da;--sel:#26354a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-family:system-ui,sans-serif;font-size:14px;z-index:2}
main{max-width:1100px;margin:0 auto;padding:16px}.q{font-family:system-ui,sans-serif;font-size:13px;color:var(--mut);margin:0 0 6px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:720px){.pair{grid-template-columns:1fr}}
.art{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}.meta{font-family:system-ui,sans-serif;font-size:12px;color:var(--mut)}
h2{font-size:19px;margin:4px 0 6px;line-height:1.3}.deck{font-style:italic;color:var(--mut);margin:0 0 8px}details{margin-top:6px;font-size:14px}
.labels{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:16px 0 8px;font-family:system-ui,sans-serif}@media(max-width:720px){.labels{grid-template-columns:1fr 1fr}}
button{font:inherit;font-size:14px;padding:10px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
button.on{background:var(--sel);border-color:var(--acc);font-weight:600}.nav{display:flex;gap:8px;justify-content:space-between;font-family:system-ui,sans-serif}
.def{font-family:system-ui,sans-serif;font-size:13px;color:var(--mut);margin:0 0 12px}kbd{font-family:ui-monospace,monospace;font-size:12px;border:1px solid var(--line);border-radius:3px;padding:0 4px}
.full{font-family:system-ui,sans-serif;font-size:14px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:4px 14px;margin:0 0 12px}
.anc{font-family:system-ui,sans-serif;font-size:14px;margin:4px 0 12px;display:flex;gap:8px;align-items:center}
.anc input{flex:1;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
</style></head><body>
<header><strong>__TITLE__</strong><span id="prog"></span><span style="flex:1"></span><button id="dl">Download labels</button></header>
<main>
<p class="def"><b>Question:</b> how do these two articles relate? Use <b>1 or 2 only if you can name one specific, dated happening both stem from</b> (the anchor); a shared policy, programme, long process, person or organisation is not an anchor. Keys <kbd>1</kbd>–<kbd>5</kbd> pick a label, <kbd>←</kbd>/<kbd>→</kbd> move. Labels are saved in this browser as you go.</p>
<details class="full"><summary>Full definition (__DEFV__, approved 28 Oct)</summary>__DEF__</details>
<div id="view"></div>
<div class="labels" id="labels"></div>
<div class="anc"><label for="anc">Anchor (optional, for 1 or 2):</label><input id="anc" placeholder="e.g. Delhi protests, 13 Oct — press Enter when done, then 1–5"></div>
<div class="nav"><button id="prev">← Previous</button><button id="next">Next unlabelled →</button></div>
</main>
<script>
const DATA = __DATA__, NAME = "__NAME__", KEY = "ni-judge-" + NAME;
const LABELS = [["same_event","Same event"],["same_story","Same story, different event"],["same_subject","Same subject only"],["unrelated","Unrelated"],["cant","Can't tell"]];
let st = {labels: {}, anchors: {}}; try { st = Object.assign(st, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) {}
const lab = st.labels, anc = st.anchors;
let i = 0;
const esc = s => (s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function art(x){ return `<div class="art"><div class="meta">${esc(x.paper)} · ${esc(x.date)} · p${x.page ?? "?"} · ${x.words} words</div>
<h2>${esc(x.head) || "<i>(no headline)</i>"}</h2>${x.deck ? `<p class="deck">${esc(x.deck)}</p>` : ""}<div>${esc(x.excerpt)}</div>
${x.rest ? `<details><summary>rest of the article</summary>${esc(x.rest)}</details>` : ""}</div>`; }
function save(){ try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }
function show(){ const p = DATA[i];
  document.getElementById("view").innerHTML = `<p class="q">${p.pair} · ${i + 1} of ${DATA.length}</p><div class="pair">${art(p.a)}${art(p.b)}</div>`;
  document.getElementById("labels").innerHTML = LABELS.map(([k, t], n) => `<button data-k="${k}" class="${lab[p.pair] === k ? "on" : ""}">${n + 1} · ${t}</button>`).join("");
  document.querySelectorAll("#labels button").forEach(b => b.onclick = () => pick(b.dataset.k));
  document.getElementById("anc").value = anc[p.pair] || "";
  document.getElementById("prog").textContent = `${Object.keys(lab).length} / ${DATA.length} labelled`; window.scrollTo(0, 0); }
document.getElementById("anc").addEventListener("keydown", e => { if (e.key === "Enter" || e.key === "Escape") e.target.blur(); });
document.getElementById("anc").addEventListener("input", e => { const v = e.target.value.trim(); if (v) anc[DATA[i].pair] = v; else delete anc[DATA[i].pair]; save(); });
function pick(k){ document.getElementById("anc").blur(); lab[DATA[i].pair] = k; save(); nextUn(); }
function nextUn(){ for (let s = 1; s <= DATA.length; s++){ const j = (i + s) % DATA.length; if (!lab[DATA[j].pair]) { i = j; return show(); } } show(); }
document.getElementById("next").onclick = nextUn;
document.getElementById("prev").onclick = () => { i = Math.max(0, i - 1); show(); };
document.getElementById("dl").onclick = () => { const blob = new Blob([JSON.stringify({sheet: NAME, definition: "__DEFV__", labels: lab, anchors: anc}, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `labels_${NAME}.json`; a.click(); };
document.addEventListener("keydown", e => { if (e.target.tagName === "INPUT") return; const n = parseInt(e.key);
  if (n >= 1 && n <= 5) pick(LABELS[n - 1][0]); if (e.key === "ArrowRight") { i = Math.min(DATA.length - 1, i + 1); show(); } if (e.key === "ArrowLeft") { i = Math.max(0, i - 1); show(); } });
show();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["sample"])
    ap.add_argument("--name", default="j1")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2028)
    ap.add_argument("--grouping", default="g0.1")
    ap.add_argument("--run", default="exam-v1")
    ap.add_argument("--tag-model", default="claude-haiku-5-5")
    ap.add_argument("--tag-prompt", default="v1.3")
    ap.add_argument("--per-date", type=int, default=25, help="v0.2: at most this many pairs from one date")
    ap.add_argument("--per-text", type=int, default=3, help="v0.2: at most this many pairs containing one article")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"sample": cmd_sample}[args.command](args)


if __name__ == "__main__":
    main()
