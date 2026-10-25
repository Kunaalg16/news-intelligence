"""
p2_dev_pairs.py -- 2.4 pair test, step 1: pick the pairs and build the blind labelling sheet (v0.2, 25 Oct 2026: + extend)

WHAT IT DOES
  Picks ~130 cross-paper article pairs by the six routes of the pre-registered protocol
  (drafts/2026-10-24_2.4_pair_test_protocol.md), with a fixed seed, and writes:
    data/pairs/pairs_<name>.jsonl      the pairs (text hashes, route, day) -- the answer sheet, never shown while labelling
    review/pairs/sheet_<name>.html     the labelling page: headline, deck, paper, date and opening of each article;
                                       NO scores, routes or model names; order shuffled; labels saved in the browser
                                       and downloaded as labels_<name>.json when done
  `relabel` makes a second sheet with 15 of the labelled pairs (seeded), for the next-day agreement check.
  `extend` (v0.2) tops up Same-event pairs when step 0 finds fewer than 30 (protocol: "add pairs from P1/P2
  by the same seeded method"): it rebuilds the pools with the same seed, checks that they reproduce the
  original pair set exactly, then draws NEW P1/P2 pairs (2:1, none already in the set) with seed + 100 into
    data/pairs/pairs_<name>x.jsonl and review/pairs/sheet_<name>x.html   (pair ids R001…, blind like the first sheet)
  Without --add it only prints the Same-event count, the rate per route so far and a suggested size.

ROUTES (protocol: P1 40 · P2 20 · P3 25 · P4 20 · P5 10 · P6 15; all cross-paper)
  P1 nominations   each model x V1: every text's nearest same-day neighbour in another paper; pooled, then sampled
  P2 entities      same day, sharing >= 2 entities (Tag v1.3 canonical names), any similarity
  P3 hard negative same day, same main topic, sharing <= 1 entity, similarity in the top 30% of same-day pairs
  P4 borderline    same day, similarity (average over models, as a rank) between the 85th and 97th percentile
  P5 random        same day, uniform
  P6 next day      nearest neighbour on the following day in another paper (pooled over models)
  A pair is taken once (first route wins, in the order above). Junk is left out before sampling:
  kind notice, or sponsored with no topic (the three-layer rule's layer 1).

HOW TO RUN (from ~/news-intel; the embedding environment has numpy)
  .venv-embed/bin/python src/p2_dev_pairs.py make --name v1            pairs + sheet (seed 2025)
  .venv-embed/bin/python src/p2_dev_pairs.py relabel --name v1         15-pair re-label sheet from labels_v1.json
  .venv-embed/bin/python src/p2_dev_pairs.py extend --name v1          estimate only (reads labels_v1.json)
  .venv-embed/bin/python src/p2_dev_pairs.py extend --name v1 --add 120   top-up sheet v1x (P1 80 + P2 40)
  Options: --seed · --models minilm,bge-m3,qwen3-0.6b (those with V1 vectors) · --tag-model · --tag-prompt
  Opens nothing and changes neither database (both read-only).
"""
import argparse, html, json, random, sqlite3, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

NEWS_DB, VEC_DB = Path("data") / "news.db", Path("data") / "vectors.db"
PAIRS_DIR, SHEET_DIR = Path("data") / "pairs", Path("review") / "pairs"
QUOTA = {"P1": 40, "P2": 20, "P3": 25, "P4": 20, "P5": 10, "P6": 15}
EXCERPT_WORDS = 140


def ro(path):
    if not path.exists():
        sys.exit(f"{path} not found. Run from ~/news-intel.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------- load ----------
def load(args):
    news, vec = ro(NEWS_DB), ro(VEC_DB)
    n = news.execute("SELECT COUNT(*) FROM tags WHERE model=? AND prompt_version=?",
                     (args.tag_model, args.tag_prompt)).fetchone()[0]
    if not n:
        have = news.execute("SELECT model, prompt_version, COUNT(*) FROM tags GROUP BY 1, 2").fetchall()
        sys.exit(f"No tags for {args.tag_model} {args.tag_prompt}. Present: {have}")
    # earliest seat per text = its paper, day and deck (same rule as the store and p2_embed)
    seat = {}
    for h, d, pub, page, deck, head, body in news.execute(
            "SELECT p.text_hash, p.date, p.publisher, p.page, p.deck, t.headline, t.body FROM placements p "
            "JOIN texts t ON t.text_hash = p.text_hash WHERE p.run_id = ? ORDER BY p.text_hash, p.date, p.publisher, p.art_id",
            (args.run,)):
        if h not in seat:
            seat[h] = {"date": d, "paper": pub, "page": page, "deck": deck or "", "head": head or "", "body": body or ""}
    tag = {h: (topic, kind) for h, topic, kind in news.execute(
        "SELECT text_hash, topic, kind FROM tags WHERE model=? AND prompt_version=?", (args.tag_model, args.tag_prompt))}
    ents = defaultdict(set)
    for h, c in news.execute("SELECT text_hash, lower(canonical) FROM entities WHERE model=? AND prompt_version=?",
                             (args.tag_model, args.tag_prompt)):
        ents[h].add(c)
    keep = [h for h in seat if h in tag and tag[h][1] != "notice" and not (tag[h][1] == "sponsored" and tag[h][0] is None)]
    models = [m for m in args.models.split(",")
              if vec.execute("SELECT COUNT(*) FROM vectors WHERE model=? AND variant='V1'", (m,)).fetchone()[0]]
    if not models:
        sys.exit("No V1 vectors for the requested models.")
    V = {}
    for m in models:
        rows = dict(vec.execute("SELECT text_hash, vec FROM vectors WHERE model=? AND variant='V1'", (m,)))
        V[m] = {h: np.frombuffer(rows[h], dtype=np.float32) for h in keep if h in rows}
    keep = [h for h in keep if all(h in V[m] for m in models)]
    print(f"texts: {len(seat)} in run, {len(keep)} usable (junk left out, vectors for {', '.join(models)})")
    return seat, tag, ents, V, models, keep


# ---------- routes ----------
def by_day(seat, keep):
    days = defaultdict(list)
    for h in keep:
        days[seat[h]["date"]].append(h)
    return dict(sorted(days.items()))


def sims(V, models, a, b):
    """Per model, cosine similarity matrix between lists a and b (vectors are normalised)."""
    return {m: np.stack([V[m][h] for h in a]) @ np.stack([V[m][h] for h in b]).T for m in models}


def key(a, b):
    return tuple(sorted((a, b)))


def build_pools(args, seat, tag, ents, V, models, keep):
    """Candidate pairs per route. Deterministic: same databases + seed -> same pools.
    Uses the seeded generator only for P5, and returns it so the first draw continues the same stream."""
    rng = random.Random(args.seed)
    days = by_day(seat, keep)
    dates = list(days)
    pools = {r: set() for r in QUOTA}
    all_same_day = []                                   # (avg rank score, pair) for P4 / P3 thresholds
    for d, hs in days.items():
        S = sims(V, models, hs, hs)
        paper = [seat[h]["paper"] for h in hs]
        n = len(hs)
        cross = np.array([[paper[i] != paper[j] for j in range(n)] for i in range(n)])
        np.fill_diagonal(cross, False)
        # average over models of each pair's percentile rank within the day (makes models comparable)
        iu = np.triu_indices(n, 1)
        ok = cross[iu]
        ranks = []
        for m in models:
            v = S[m][iu][ok]
            ranks.append(v.argsort().argsort() / max(1, len(v) - 1))
        avg = np.mean(ranks, axis=0)
        pairs = [(hs[i], hs[j]) for i, j, o in zip(*iu, ok) if o]
        all_same_day += list(zip(avg, pairs))
        for m in models:                                # P1: nearest other-paper neighbour per text, per model
            M = np.where(cross, S[m], -9)
            for i in range(n):
                j = int(M[i].argmax())
                if M[i, j] > -9:
                    pools["P1"].add(key(hs[i], hs[j]))
        for (a, b) in pairs:                            # P2 entities
            if len(ents[a] & ents[b]) >= 2:
                pools["P2"].add(key(a, b))
    hi30 = np.quantile([s for s, _ in all_same_day], 0.70)
    lo85, hi97 = np.quantile([s for s, _ in all_same_day], [0.85, 0.97])
    for s, (a, b) in all_same_day:
        if s >= hi30 and tag[a][0] and tag[a][0] == tag[b][0] and len(ents[a] & ents[b]) <= 1:
            pools["P3"].add(key(a, b))
        if lo85 <= s <= hi97:
            pools["P4"].add(key(a, b))
    pools["P5"] = {key(a, b) for _, (a, b) in rng.sample(all_same_day, min(500, len(all_same_day)))}
    for d0, d1 in zip(dates, dates[1:]):                 # P6 next day (consecutive dates in the archive)
        a, b = days[d0], days[d1]
        S = sims(V, models, a, b)
        for m in models:
            for i, h in enumerate(a):
                order = np.argsort(-S[m][i])
                for j in order:
                    if seat[b[j]]["paper"] != seat[h]["paper"] and b[j] != h:
                        pools["P6"].add((h, b[j]))
                        break
    return pools, rng


def make_pairs(args, seat, tag, ents, V, models, keep):
    pools, rng = build_pools(args, seat, tag, ents, V, models, keep)
    taken, out = set(), []
    for r, q in QUOTA.items():
        cand = sorted(pools[r] - taken)
        pick = rng.sample(cand, min(q, len(cand)))
        if len(pick) < q:
            print(f"  note: route {r} has only {len(cand)} candidates (wanted {q})")
        for a, b in pick:
            taken.add(key(a, b))
            out.append({"route": r, "a": a, "b": b, "day_a": seat[a]["date"], "day_b": seat[b]["date"]})
    rng.shuffle(out)
    for i, p in enumerate(out, 1):
        p["pair"] = f"Q{i:03d}"
        if rng.random() < 0.5:                          # random left/right, so position says nothing
            p["a"], p["b"], p["day_a"], p["day_b"] = p["b"], p["a"], p["day_b"], p["day_a"]
    print("pairs: " + " · ".join(f"{r} {sum(p['route'] == r for p in out)} (pool {len(pools[r])})" for r in QUOTA)
          + f" · total {len(out)}")
    return out, pools


# ---------- sheet ----------
def card(s):
    words = s["body"].split()
    ex = " ".join(words[:EXCERPT_WORDS]) + (" …" if len(words) > EXCERPT_WORDS else "")
    return {"paper": s["paper"], "date": s["date"], "page": s["page"], "head": s["head"], "deck": s["deck"],
            "excerpt": ex, "words": len(words), "rest": " ".join(words[EXCERPT_WORDS:])}


def write_sheet(path, name, pairs, seat, title):
    data = [{"pair": p["pair"], "a": card(seat[p["a"]]), "b": card(seat[p["b"]])} for p in pairs]
    page = SHEET.replace("__TITLE__", html.escape(title)).replace("__NAME__", name) \
                .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    print(f"sheet: {path}  ({len(data)} pairs) -- open it in your browser")


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
</style></head><body>
<header><strong>__TITLE__</strong><span id="prog"></span><span style="flex:1"></span><button id="dl">Download labels</button></header>
<main>
<p class="def"><b>Question:</b> would these two articles belong in the <b>same digest item</b>? Keys <kbd>1</kbd>–<kbd>5</kbd> pick a label, <kbd>←</kbd>/<kbd>→</kbd> move. Labels are saved in this browser as you go.</p>
<div id="view"></div>
<div class="labels" id="labels"></div>
<div class="nav"><button id="prev">← Previous</button><button id="next">Next unlabelled →</button></div>
</main>
<script>
const DATA = __DATA__, NAME = "__NAME__", KEY = "ni-pairs-" + NAME;
const LABELS = [["same","Same event"],["follow","Follow-up"],["subject","Same subject, different event"],["unrelated","Unrelated"],["cant","Can't tell"]];
let lab = {}; try { lab = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
let i = 0;
const esc = s => (s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function art(x){ return `<div class="art"><div class="meta">${esc(x.paper)} · ${esc(x.date)} · p${x.page ?? "?"} · ${x.words} words</div>
<h2>${esc(x.head) || "<i>(no headline)</i>"}</h2>${x.deck ? `<p class="deck">${esc(x.deck)}</p>` : ""}<div>${esc(x.excerpt)}</div>
${x.rest ? `<details><summary>rest of the article</summary>${esc(x.rest)}</details>` : ""}</div>`; }
function save(){ try { localStorage.setItem(KEY, JSON.stringify(lab)); } catch (e) {} }
function show(){ const p = DATA[i];
  document.getElementById("view").innerHTML = `<p class="q">${p.pair} · ${i + 1} of ${DATA.length}</p><div class="pair">${art(p.a)}${art(p.b)}</div>`;
  document.getElementById("labels").innerHTML = LABELS.map(([k, t], n) => `<button data-k="${k}" class="${lab[p.pair] === k ? "on" : ""}">${n + 1} · ${t}</button>`).join("");
  document.querySelectorAll("#labels button").forEach(b => b.onclick = () => pick(b.dataset.k));
  document.getElementById("prog").textContent = `${Object.keys(lab).length} / ${DATA.length} labelled`; window.scrollTo(0, 0); }
function pick(k){ lab[DATA[i].pair] = k; save(); nextUn(); }
function nextUn(){ for (let s = 1; s <= DATA.length; s++){ const j = (i + s) % DATA.length; if (!lab[DATA[j].pair]) { i = j; return show(); } } show(); }
document.getElementById("next").onclick = nextUn;
document.getElementById("prev").onclick = () => { i = Math.max(0, i - 1); show(); };
document.getElementById("dl").onclick = () => { const blob = new Blob([JSON.stringify({sheet: NAME, labels: lab}, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `labels_${NAME}.json`; a.click(); };
document.addEventListener("keydown", e => { if (e.target.tagName === "INPUT") return; const n = parseInt(e.key);
  if (n >= 1 && n <= 5) pick(LABELS[n - 1][0]); if (e.key === "ArrowRight") { i = Math.min(DATA.length - 1, i + 1); show(); } if (e.key === "ArrowLeft") { i = Math.max(0, i - 1); show(); } });
show();
</script></body></html>"""


# ---------- commands ----------
def cmd_make(args):
    out = PAIRS_DIR / f"pairs_{args.name}.jsonl"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists. A new pair set needs a new --name (or --force to overwrite, before any labelling).")
    seat, tag, ents, V, models, keep = load(args)
    pairs, _ = make_pairs(args, seat, tag, ents, V, models, keep)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {"seed": args.seed, "models": models, "variant": "V1", "run": args.run,
                                     "tags": f"{args.tag_model} {args.tag_prompt}", "quota": QUOTA}}) + "\n")
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    print(f"pairs: {out}")
    write_sheet(SHEET_DIR / f"sheet_{args.name}.html", args.name, pairs, seat, f"Pair labelling · {args.name}")


def cmd_relabel(args):
    lines = (PAIRS_DIR / f"pairs_{args.name}.jsonl").read_text(encoding="utf-8").splitlines()
    pairs = [json.loads(l) for l in lines[1:]]
    labels = json.loads((SHEET_DIR / f"labels_{args.name}.json").read_text(encoding="utf-8"))["labels"]
    done = [p for p in pairs if p["pair"] in labels]
    pick = random.Random(args.seed + 1).sample(done, min(15, len(done)))
    news = ro(NEWS_DB)
    seat = {}
    for p in pick:
        for h in (p["a"], p["b"]):
            r = news.execute("SELECT p.date, p.publisher, p.page, p.deck, t.headline, t.body FROM placements p JOIN texts t "
                             "ON t.text_hash = p.text_hash WHERE p.text_hash = ? AND p.run_id = ? "
                             "ORDER BY p.date, p.publisher, p.art_id LIMIT 1", (h, args.run)).fetchone()
            seat[h] = {"date": r[0], "paper": r[1], "page": r[2], "deck": r[3] or "", "head": r[4] or "", "body": r[5] or ""}
    random.Random(args.seed + 2).shuffle(pick)
    write_sheet(SHEET_DIR / f"sheet_{args.name}_relabel.html", f"{args.name}_relabel", pick, seat,
                f"Re-label · {args.name} (15 pairs)")


def read_pairs(name):
    path = PAIRS_DIR / f"pairs_{name}.jsonl"
    if not path.exists():
        sys.exit(f"{path} not found.")
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0])["meta"], [json.loads(l) for l in lines[1:]]


def cmd_extend(args):
    meta, base = read_pairs(args.name)
    if meta["seed"] != args.seed or meta["run"] != args.run or ",".join(meta["models"]) != args.models:
        sys.exit(f"settings differ from pairs_{args.name}.jsonl ({meta}); run with the same --seed, --run, --models")
    lab_path = SHEET_DIR / f"labels_{args.name}.json"
    if not lab_path.exists():
        sys.exit(f"{lab_path} not found (download it from the sheet and move it there first).")
    labels = json.loads(lab_path.read_text(encoding="utf-8"))["labels"]
    same = sum(v == "same" for v in labels.values())
    rate = {}
    for r in ("P1", "P2"):
        ps = [p for p in base if p["route"] == r and p["pair"] in labels]
        rate[r] = (sum(labels[p["pair"]] == "same" for p in ps), len(ps))
    hit, n = rate["P1"][0] + rate["P2"][0], rate["P1"][1] + rate["P2"][1]
    print(f"labels_{args.name}: {len(labels)} labelled, Same event {same} (protocol needs >= 30)")
    print(f"Same-event rate so far: P1 {rate['P1'][0]}/{rate['P1'][1]} · P2 {rate['P2'][0]}/{rate['P2'][1]}")
    need = max(0, 30 - same)
    if need and hit:
        est = int(need / (hit / n) * 1.25 + 0.999)
        print(f"to find {need} more at that rate (+25% margin): about {est} new pairs")
    if not args.add:
        print("estimate only; nothing written. Add --add N to build the top-up sheet.")
        return
    ext = f"{args.name}x"
    out = PAIRS_DIR / f"pairs_{ext}.jsonl"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists. Refusing to overwrite (labels may depend on it).")
    seat, tag, ents, V, models, keep = load(args)
    redo, pools = make_pairs(args, seat, tag, ents, V, models, keep)
    same_set = {(p["route"], key(p["a"], p["b"])) for p in redo} == {(p["route"], key(p["a"], p["b"])) for p in base}
    print("reproduction of the original pair set: " + ("exact" if same_set else
          "DIFFERENT (databases changed since?); continuing, the original file is what is excluded"))
    taken = {key(p["a"], p["b"]) for p in base}
    rng = random.Random(args.seed + 100)
    want = {"P1": args.add - args.add // 3, "P2": args.add // 3}
    new = []
    for r, q in want.items():
        cand = sorted(pools[r] - taken)
        pick = rng.sample(cand, min(q, len(cand)))
        if len(pick) < q:
            print(f"  note: route {r} has only {len(cand)} new candidates (wanted {q})")
        for a, b in pick:
            taken.add(key(a, b))
            new.append({"route": r, "a": a, "b": b, "day_a": seat[a]["date"], "day_b": seat[b]["date"]})
    rng.shuffle(new)
    for i, p in enumerate(new, 1):
        p["pair"] = f"R{i:03d}"
        if rng.random() < 0.5:
            p["a"], p["b"], p["day_a"], p["day_b"] = p["b"], p["a"], p["day_b"], p["day_a"]
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {**meta, "extends": args.name, "seed_extend": args.seed + 100, "added": want,
                                     "reproduced": same_set}}) + "\n")
        for p in new:
            f.write(json.dumps(p) + "\n")
    print(f"new pairs: " + " · ".join(f"{r} {sum(p['route'] == r for p in new)}" for r in want)
          + f" · total {len(new)} -> {out}")
    write_sheet(SHEET_DIR / f"sheet_{ext}.html", ext, new, seat, f"Pair labelling · {ext}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["make", "relabel", "extend"])
    ap.add_argument("--name", default="v1")
    ap.add_argument("--seed", type=int, default=2025)
    ap.add_argument("--run", default="exam-v1")
    ap.add_argument("--models", default="minilm,bge-m3,qwen3-0.6b")
    ap.add_argument("--tag-model", default="claude-haiku-5-5")
    ap.add_argument("--tag-prompt", default="v1.3")
    ap.add_argument("--add", type=int, default=0, help="extend: number of new pairs (P1 2/3, P2 1/3)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    {"make": cmd_make, "relabel": cmd_relabel, "extend": cmd_extend}[args.command](args)


if __name__ == "__main__":
    main()
