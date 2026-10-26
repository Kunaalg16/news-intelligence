"""p2_dev_boundary.py · 2.4 dev tool · "same event" boundary review (STATUS Next 1c)

WHAT   Compares Opus's pair labels with Kunaal's blind labels on the pair sets
       (v1, v1x), prints agreement and Cohen's kappa on the yes/no question
       "Same event?", and builds a review sheet of every pair where the two
       disagree on that question.
WHY    Opus and Kunaal split on "same event" vs "same subject" (kappa 0.49 on v1,
       0.42 on v1x in chat 16). Kunaal reviews the disagreements, says why each
       is or isn't the same event, and that becomes the written definition for
       the Haiku judge in story grouping. The kappa printed here is the baseline
       for the re-test (pass line kappa >= 0.70, set 25 Oct).
WHEN   Once, after the 2.4 decision (the re-label is done, so Kunaal may now see
       Opus's labels). Again only if a set or a label file changes.
HOW    cd ~/news-intel && .venv/bin/python src/p2_dev_boundary.py
         reads   review/pairs/sheet_<set>.html   (the article cards, as labelled)
                 review/pairs/labels_<set>.json  (Kunaal)
                 data/pairs/opus_<set>.json      (Opus)
         writes  review/pairs/sheet_boundary.html (refuses to overwrite; --force)
       --sets v1,v1x   --dry-run (print only, write nothing)
       Reads only; never touches news.db or vectors.db. Standard library only.
"""
import argparse, html, json, os, re, sys

LABELS = ["same", "follow", "subject", "unrelated", "cant"]
NAMES = {"same": "Same event", "follow": "Follow-up", "subject": "Same subject, different event",
         "unrelated": "Unrelated", "cant": "Can't tell"}
ID = re.compile(r"^[A-Z]\d{3}$")


def die(msg):
    sys.exit("STOP: " + msg)


def load_json(path):
    if not os.path.exists(path):
        die(f"{path} not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_labels(obj, path):
    """Return {pair_id: label} from a label file, whatever its wrapper.
    Takes the largest dict whose keys are pair ids and whose values are label
    keys (or dicts holding a 'label'). Fails loudly if none is found."""
    best = {}

    def norm(v):
        if isinstance(v, dict):
            v = v.get("label")
        return v if v in LABELS else None

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            ids = {k: norm(v) for k, v in o.items() if ID.match(str(k))}
            ids = {k: v for k, v in ids.items() if v}
            if len(ids) > len(best):
                best = ids
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            if o and all(isinstance(x, dict) and "pair" in x and norm(x) for x in o):
                d = {x["pair"]: norm(x) for x in o}
                if len(d) > len(best):
                    best = d
            for v in o:
                walk(v)

    walk(obj)
    if not best:
        top = list(obj)[:10] if isinstance(obj, dict) else type(obj).__name__
        die(f"no pair labels found in {path} (top-level keys: {top})")
    return best


def find_audit(obj):
    """Pair ids listed under any key containing 'audit' (v1x triage record), else empty."""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if "audit" in str(k).lower() and isinstance(v, list):
                    out.update(x if isinstance(x, str) else x.get("pair", "") for x in v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return {x for x in out if ID.match(x)}


def sheet_cards(path):
    if not os.path.exists(path):
        die(f"{path} not found")
    s = open(path, encoding="utf-8").read()
    m = re.search(r"const DATA = (\[.*?\]), NAME", s, re.S)
    if not m:
        die(f"could not read the article cards in {path}")
    return {p["pair"]: p for p in json.loads(m.group(1))}


def kappa(rows):
    """rows: list of (kunaal_yes, opus_yes). Returns n, agreement, chance, kappa."""
    n = len(rows)
    if n == 0:
        return 0, 0.0, 0.0, float("nan")
    po = sum(a == b for a, b in rows) / n
    pk = sum(a for a, _ in rows) / n
    pm = sum(b for _, b in rows) / n
    pe = pk * pm + (1 - pk) * (1 - pm)
    k = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return n, po, pe, k


def report(name, kun, opus, only=None):
    ids = sorted(set(kun) & set(opus))
    if only is not None:
        ids = [i for i in ids if i in only]
    ids = [i for i in ids if kun[i] != "cant" and opus[i] != "cant"]
    n, po, pe, k = kappa([(kun[i] == "same", opus[i] == "same") for i in ids])
    print(f"  {name:<28} n {n:>3} · agreement {po*100:5.1f}% · chance {pe*100:5.1f}% · kappa {k:.2f}")
    return ids


def confusion(kun, opus, ids):
    print("    Kunaal (rows) x Opus (columns):  " + "  ".join(f"{l[:7]:>7}" for l in LABELS))
    for a in LABELS:
        row = [sum(1 for i in ids if kun[i] == a and opus[i] == b) for b in LABELS]
        if sum(row):
            print(f"    {a:<32} " + "  ".join(f"{c:>7}" for c in row))


def build_sheet(items):
    data = json.dumps(items, ensure_ascii=False)
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Same-event boundary review</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#deded8;--acc:#2f5d8a;--sel:#e3ecf5;--k:#7a4b00;--o:#2f5d8a}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#a3a29c;--card:#1f1f1d;--line:#3a3a36;--acc:#8fb4da;--sel:#26354a;--k:#e0b46a;--o:#8fb4da}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-family:system-ui,sans-serif;font-size:14px;z-index:2}
main{max-width:1100px;margin:0 auto;padding:16px}.q{font-family:system-ui,sans-serif;font-size:13px;color:var(--mut);margin:0 0 6px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:720px){.pair{grid-template-columns:1fr}}
.art{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}.meta{font-family:system-ui,sans-serif;font-size:12px;color:var(--mut)}
h2{font-size:19px;margin:4px 0 6px;line-height:1.3}.deck{font-style:italic;color:var(--mut);margin:0 0 8px}details{margin-top:6px;font-size:14px}
.who{font-family:system-ui,sans-serif;font-size:15px;margin:14px 0 6px}.who b.k{color:var(--k)}.who b.o{color:var(--o)}
.labels{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0;font-family:system-ui,sans-serif}
button{font:inherit;font-size:14px;padding:10px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
button.on{background:var(--sel);border-color:var(--acc);font-weight:600}.nav{display:flex;gap:8px;justify-content:space-between;font-family:system-ui,sans-serif}
textarea{width:100%;min-height:70px;font:15px/1.4 system-ui,sans-serif;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.def{font-family:system-ui,sans-serif;font-size:13px;color:var(--mut);margin:0 0 12px}
</style></head><body>
<header><strong>Same-event boundary review</strong><span id="prog"></span><span style="flex:1"></span><button id="dl">Download notes</button></header>
<main>
<p class="def">You and Opus disagreed on whether these are the <b>same event</b>. Your blind label and Opus's are both shown. Pick your verdict now and say in a few words <b>why</b> (what makes it the same event, or a different one). Saved in this browser as you go; you can also just dictate \"B03: different event because …\" in the chat.</p>
<div id="view"></div>
<div class="labels" id="labels"></div>
<textarea id="why" placeholder="Why? (a few words)"></textarea>
<div class="nav" style="margin-top:10px"><button id="prev">&larr; Previous</button><button id="next">Next &rarr;</button></div>
</main>
<script>
const DATA = """ + data + """;
const KEY = "ni-boundary-v1";
const V = [["same","Same event"],["different","Different event"],["unsure","Unsure"]];
let notes = {}; try { notes = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
let i = 0;
const esc = s => String(s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function save(){ try { localStorage.setItem(KEY, JSON.stringify(notes)); } catch (e) {} }
function art(x){ return `<div class="art"><div class="meta">${esc(x.paper)} · ${esc(x.date)} · p${esc(x.page)}</div><h2>${esc(x.head) || "<i>(no headline)</i>"}</h2>${x.deck ? `<p class="deck">${esc(x.deck)}</p>` : ""}<div>${esc(x.excerpt)}</div>${x.rest ? `<details><summary>rest of the article</summary>${esc(x.rest)}</details>` : ""}</div>`; }
function show(){
  const p = DATA[i], n = notes[p.id] || {};
  document.getElementById("view").innerHTML = `<p class="q">${p.id} · ${i + 1} of ${DATA.length} · pair ${p.pair} (${p.set})</p><div class="pair">${art(p.a)}${art(p.b)}</div><p class="who">You (blind): <b class="k">${esc(p.kunaal)}</b> · Opus: <b class="o">${esc(p.opus)}</b></p>`;
  document.getElementById("labels").innerHTML = V.map(([k, t]) => `<button data-k="${k}" class="${n.verdict === k ? "on" : ""}">${t}</button>`).join("");
  document.querySelectorAll("#labels button").forEach(b => b.onclick = () => { notes[p.id] = Object.assign({pair: p.pair, set: p.set}, notes[p.id] || {}, {verdict: b.dataset.k}); save(); show(); });
  const w = document.getElementById("why"); w.value = n.why || "";
  w.oninput = () => { notes[p.id] = Object.assign({pair: p.pair, set: p.set}, notes[p.id] || {}, {why: w.value}); save(); prog(); };
  prog();
}
function prog(){ const d = DATA.filter(p => (notes[p.id] || {}).verdict).length; document.getElementById("prog").textContent = `${d} of ${DATA.length} decided`; }
document.getElementById("prev").onclick = () => { i = Math.max(0, i - 1); show(); };
document.getElementById("next").onclick = () => { i = Math.min(DATA.length - 1, i + 1); show(); };
document.getElementById("dl").onclick = () => { const blob = new Blob([JSON.stringify({sheet: "boundary", notes}, null, 1)], {type: "application/json"}); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "boundary_notes.json"; a.click(); };
show();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sets", default="v1,v1x")
    ap.add_argument("--root", default=".")
    ap.add_argument("--force", action="store_true", help="overwrite an existing sheet_boundary.html")
    ap.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    a = ap.parse_args()
    R = lambda *p: os.path.join(a.root, *p)

    out = R("review", "pairs", "sheet_boundary.html")
    if os.path.exists(out) and not (a.force or a.dry_run):
        die(f"{out} exists (it may hold your work in progress); use --force to rebuild")

    print("== same-event boundary review · Opus vs Kunaal · yes/no on Same event (Can't tell left out)")
    items, allrows = [], []
    for s in [x.strip() for x in a.sets.split(",") if x.strip()]:
        kun = find_labels(load_json(R("review", "pairs", f"labels_{s}.json")), f"labels_{s}.json")
        oraw = load_json(R("data", "pairs", f"opus_{s}.json"))
        opus = find_labels(oraw, f"opus_{s}.json")
        cards = sheet_cards(R("review", "pairs", f"sheet_{s}.html"))
        audit = find_audit(oraw)
        print(f"-- {s}: Kunaal {len(kun)} labels · Opus {len(opus)} labels · cards {len(cards)}"
              + (f" · audit list {len(audit)}" if audit else ""))
        ids = report(f"{s} (all labelled by both)", kun, opus)
        if audit:
            report(f"{s} (without the audit)", kun, opus, only=set(ids) - audit)
        confusion(kun, opus, ids)
        allrows += [(kun[i] == "same", opus[i] == "same") for i in ids]
        for i in ids:
            if (kun[i] == "same") != (opus[i] == "same"):
                if i not in cards:
                    die(f"pair {i} has labels but no card in sheet_{s}.html")
                c = cards[i]
                items.append({"pair": i, "set": s, "a": c["a"], "b": c["b"],
                              "kunaal": NAMES[kun[i]], "opus": NAMES[opus[i]],
                              "dir": "opus_same" if opus[i] == "same" else "kunaal_same"})
    n, po, pe, k = kappa(allrows)
    print(f"  {'all sets pooled':<28} n {n:>3} · agreement {po*100:5.1f}% · chance {pe*100:5.1f}% · kappa {k:.2f}"
          "   (baseline for the re-test; pass line 0.70)")

    items.sort(key=lambda x: (x["dir"] != "opus_same", x["set"], x["pair"]))
    for j, x in enumerate(items, 1):
        x["id"] = f"B{j:02d}"
    d1 = sum(x["dir"] == "opus_same" for x in items)
    print(f"-- disagreements {len(items)}: Opus same / you not {d1} · you same / Opus not {len(items) - d1}")
    if a.dry_run:
        print("dry run: nothing written")
        return
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_sheet(items))
    print(f"wrote {out} ({len(items)} pairs, B01-B{len(items):02d}). Nothing else was written.")


if __name__ == "__main__":
    main()
