"""p3_dev_tracksheet.py — Phase 3 dev tool · blind check of stage-2 cross-day links.
  v0.1 28 Oct 2026 (B·3)

WHAT IT DOES
  make   Draws a seeded random sample of stage-2 decisions from data/intel.db:
         N links the machine MADE (parent links) and N pairs it just REJECTED (the best
         earlier candidate of a group with no parent link, average score in [BAND_LOW, line)).
         Shuffles them together and writes a blind labelling sheet (no scores, no roles)
         plus a hidden answer key:
           review/threads/sheet_<name>.html        open in a browser, label, press Save
           data/threads/sheet_<name>_key.json      which pair is which (do NOT open before scoring)
         Refuses to overwrite an existing key (a re-draw after seeing pairs would break blindness).
  score  Reads Kunaal's labels file + the key and applies the pass lines written before
         any result (28 Oct, B·3; drafts/2026-10-28_B3_stage2.md):
           made links:      ≥ 90% "same story continuing" or "follow-up"  (= 18 of 20)  → PASS
           rejected pairs:  ≥ 40% "same story continuing" or "follow-up"  (=  8 of 20)  → line TOO STRICT
         "can't tell" pairs are left out of both shares (rule fixed before labelling).

WHY
  Stage 2 (p3_group.py track, t0.1) links a day-group to an earlier day-group when the
  average similarity is ≥ 0.810, a line chosen without cross-day evidence (S6 D4). Only a
  person reading the articles can say whether its links are right (precision) and whether it
  misses real continuations (recall). The labels also start the cross-day answer key (S6 D4).

DATA
  reads   data/intel.db, data/news.db       (read-only)
  writes  review/threads/sheet_<name>.html, data/threads/sheet_<name>_key.json   (make only)
  Kunaal's labels: the sheet downloads labels_<name>.json → move it to review/threads/ (hand-made: back up).

HOW TO RUN (from ~/news-intel, pipeline environment)
  .venv/bin/python src/p3_dev_tracksheet.py make  [--name t01] [--seed 2810] [--n 20]
  .venv/bin/python src/p3_dev_tracksheet.py score [--name t01] [--labels review/threads/labels_t01.json]
"""

import argparse
import html
import json
import random
import sqlite3
import sys
from datetime import date as Date, datetime, timezone
from pathlib import Path

NEWS_DB, INTEL_DB = Path("data/news.db"), Path("data/intel.db")
REVIEW_DIR, KEY_DIR = Path("review/threads"), Path("data/threads")
GVERSION, TVERSION = "g0.1", "t0.1"
RUN_ID = "exam-v1"
LINK_LINE = 0.810
BAND_LOW = 0.70          # "just below the line" = average in [0.70, 0.810). Chosen 28 Oct before sampling.
PASS_PRECISION = 0.90    # 18 of 20
TOO_STRICT = 0.40        # 8 of 20
OPENING_WORDS = 70       # words of body shown per side
CODEBOOK_VERSION = "cross-day v0.1 (draft, 28 Oct, B·3)"

LABELS = [  # (key, text on the sheet, one-line reminder under it)
    ("continuing", "Same story continuing",
     "The same happening, or the next stage of the same bounded episode."),
    ("followup", "Follow-up",
     "A NEW happening on the later day, taken in response to / caused by the earlier one."),
    ("subject", "Same subject only",
     "Same theme, but you cannot name one dated happening both stem from."),
    ("unrelated", "Unrelated", "No shared subject."),
    ("cant_tell", "Can't tell", "Text cut off or too little to judge (left out of the count)."),
]
LINKED = {"continuing", "followup"}

# The codebook shown on the sheet. Same vocabulary as the stage-1 definition v1.0
# (drafts/2026-10-28_B2.4_judge_definition_and_test.md §1); the cross-day ladder is S6 D1.
CODEBOOK = """
<h3>What you are judging</h3>
<p>Each pair is two story groups from <b>two different days</b>, 1 to 7 days apart: the
<b>earlier</b> one on the left, the <b>later</b> one on the right. Judge how the later one relates
to the earlier one. If a side has several articles, judge the story as a whole (the longest
article is shown; the others are listed under it).</p>

<h3>The idea behind the labels</h3>
<p>News is organised around <b>happenings</b>: specific, dated occurrences such as a decision, an
announcement, a ruling, a release of figures, an incident, an arrest, a protest action, a speech,
a match, a vote, a deal or a filing. A <b>story</b> is one such happening together with the further
happenings directly tied to it. A <b>subject</b> is a broader theme (a policy, a programme, a long
process, a condition, an industry, an organisation, a person) under which many separate stories fall.
<span class="src">Same wording as the stage-1 definition v1.0 (B·2.4).</span></p>

<h3>The labels, from closest to farthest</h3>
<ol>
<li><b>Same story continuing</b> — the later article reports <b>the same happening still
unfolding</b>, or <b>the next stage of the same bounded episode</b>: something with a clear start and
end that both articles are part of (one IPO from price band to listing; one trial's hearings;
one strike from call to end; one storm; one series of talks). New facts about the same happening
count (an updated toll; a bill passed after being tabled).
<br><i>Real example:</i> "Jio draws over $20 bn IPO bids" (14 Oct) → "Jio Platforms IPO fully
subscribed in 100 mins" (22 Oct): two stages of one IPO.</li>
<li><b>Follow-up</b> — the later article reports a <b>new happening that responds to or is caused by
the earlier one</b>: a reaction, a decision taken because of it, an investigation opened after an
incident, a market move after an announcement.
<br><i>Example:</i> the RBI holds rates on Wednesday → on Friday banks announce they will keep
lending rates unchanged.
<span class="src">Basis: a follow-up is a response on a later day (Kunaal's B33 / B37 reasons; S6 D1).</span></li>
<li><b>Same subject only</b> — the articles share a theme, but the later one does <b>not</b> stem from
the earlier one's dated happening.
<br><i>Examples:</i> two different companies' IPOs; Monday's market report and Thursday's;
September's inflation figures and October's trade figures; this week's and last week's issue of a
weekly column.</li>
<li><b>Unrelated</b> — no shared subject.</li>
<li><b>Can't tell</b> — a text is cut off, garbled, or too short to judge. Left out of the count.</li>
</ol>

<h3>How to decide between 1, 2 and 3: name the earlier happening</h3>
<p>Ask: <i>what dated happening does the earlier article report?</i> (its anchor, e.g. "Jio IPO
bidding, 14 Oct").</p>
<ul>
<li>Is the later article about <b>that same happening</b> (or the same bounded episode, one stage
further)? → <b>1 · continuing</b>.</li>
<li>Is it about <b>a different happening that exists because of it</b> (someone else acting,
reacting or being affected)? → <b>2 · follow-up</b>.</li>
<li>Is the only link a <b>theme</b> — a policy, a market, a company, a person — with no line of cause
from the earlier happening? → <b>3 · same subject only</b>.</li>
</ul>
<p>A long-running process on its own (a policy, a movement, a company's fortunes) is <b>not</b> a
bounded episode: two articles that share only that are <b>same subject only</b>.
<span class="src">The anchor rule is the stage-1 definition's (S6 D1); "bounded episode" is new in
this cross-day draft and is what this sheet tests.</span></p>

<h3>Why this matters</h3>
<p>The digest will show labels 1 and 2 as <i>"earlier coverage"</i> under today's story. Labels 3
and 4 must never be shown that way. Your labels decide whether the machine's line (now 0.810) is
right, too loose or too strict.</p>
<p class="src">Codebook: CODEBOOK_VERSION. Optional note per pair: write the anchor or anything that made
you hesitate; notes help write the final rule.</p>
""".replace("CODEBOOK_VERSION", CODEBOOK_VERSION)


def open_ro(path):
    if not path.exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def weekday(d):
    return Date.fromisoformat(d).strftime("%a %d %b %Y")


def group_card(news, intel, gid, gdate):
    """The group's longest article (headline, deck, opening, papers) + the other members' headlines."""
    arts = []
    for (h,) in intel.execute("SELECT text_hash FROM members WHERE version=? AND group_id=?", (GVERSION, gid)):
        row = news.execute("SELECT headline, body, words FROM texts WHERE text_hash=?", (h,)).fetchone()
        pubs = [p for (p,) in news.execute(
            "SELECT DISTINCT publisher FROM placements WHERE run_id=? AND date=? AND text_hash=? ORDER BY publisher",
            (RUN_ID, gdate, h))]
        deck = news.execute(
            "SELECT deck FROM placements WHERE run_id=? AND date=? AND text_hash=? AND deck IS NOT NULL AND deck != '' LIMIT 1",
            (RUN_ID, gdate, h)).fetchone() if has_deck(news) else None
        if row:
            arts.append({"headline": (row[0] or "(no headline)").strip(), "body": row[1] or "",
                         "words": row[2] or 0, "papers": pubs, "deck": (deck[0] if deck else "")})
    arts.sort(key=lambda a: (-a["words"], a["headline"]))
    lead = arts[0]
    words = lead["body"].split()
    opening = " ".join(words[:OPENING_WORDS]) + (" …" if len(words) > OPENING_WORDS else "")
    return {"date": gdate, "day": weekday(gdate), "headline": lead["headline"], "deck": lead["deck"],
            "opening": opening, "papers": lead["papers"], "words": lead["words"],
            "others": [f'{a["headline"]} ({", ".join(a["papers"])})' for a in arts[1:]]}


_deck = None


def has_deck(news):
    global _deck
    if _deck is None:
        _deck = any(r[1] == "deck" for r in news.execute("PRAGMA table_info(placements)"))
    return _deck


def cmd_make(args):
    news, intel = open_ro(NEWS_DB), open_ro(INTEL_DB)
    key_path = KEY_DIR / f"sheet_{args.name}_key.json"
    sheet_path = REVIEW_DIR / f"sheet_{args.name}.html"
    if key_path.exists():
        sys.exit(f"{key_path} exists: refusing to re-draw (blindness). Use a new --name for a new sheet.")

    made = intel.execute(
        "SELECT date, group_id, earlier_date, earlier_group_id, gap_days, avg_score, best_score, centroid_score "
        "FROM links WHERE gversion=? AND tversion=? AND role='parent' ORDER BY date, group_id",
        (GVERSION, TVERSION)).fetchall()
    rejected = intel.execute(
        "SELECT l.date, l.group_id, l.earlier_date, l.earlier_group_id, l.gap_days, l.avg_score, l.best_score, "
        "l.centroid_score FROM links l WHERE l.gversion=? AND l.tversion=? AND l.role='below' AND l.rank=1 "
        "AND l.avg_score >= ? AND l.avg_score < ? AND NOT EXISTS (SELECT 1 FROM links p WHERE p.gversion=l.gversion "
        "AND p.tversion=l.tversion AND p.group_id=l.group_id AND p.role='parent') ORDER BY l.date, l.group_id",
        (GVERSION, TVERSION, BAND_LOW, LINK_LINE)).fetchall()
    if len(made) < args.n or len(rejected) < args.n:
        sys.exit(f"pool too small: made {len(made)}, rejected-in-band {len(rejected)}, need {args.n} each")

    rng = random.Random(args.seed)
    picks = [("made", r) for r in rng.sample(made, args.n)] + [("rejected", r) for r in rng.sample(rejected, args.n)]
    rng.shuffle(picks)

    pairs, key = [], {}
    for k, (role, (d, gid, ed, egid, gap, avg, best, cen)) in enumerate(picks, start=1):
        pairs.append({"n": k, "gap": gap, "earlier": group_card(news, intel, egid, ed),
                      "later": group_card(news, intel, gid, d)})
        key[str(k)] = {"role": role, "group_id": gid, "date": d, "earlier_group_id": egid, "earlier_date": ed,
                       "gap_days": gap, "avg_score": avg, "best_score": best, "centroid_score": cen}

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"sheet": args.name, "seed": args.seed, "n_each": args.n, "gversion": GVERSION, "tversion": TVERSION,
            "band": [BAND_LOW, LINK_LINE], "pool_made": len(made), "pool_rejected_in_band": len(rejected),
            "codebook": CODEBOOK_VERSION, "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    key_path.write_text(json.dumps({"meta": meta, "pairs": key}, indent=1))
    sheet_path.write_text(render(args.name, pairs))
    print(f"sheet  {sheet_path}   ({len(pairs)} pairs, shuffled; no scores or roles inside)")
    print(f"key    {key_path}   (do not open before scoring)")
    print(f"pools  made links {len(made)} · rejected in [{BAND_LOW}, {LINK_LINE}) {len(rejected)} · seed {args.seed}")


def render(name, pairs):
    def side(c, label):
        others = ""
        if c["others"]:
            items = "".join(f"<li>{html.escape(o)}</li>" for o in c["others"])
            others = (f'<details class="others"><summary>+ {len(c["others"])} other article(s) in this story'
                      f'</summary><ul>{items}</ul></details>')
        deck = f'<p class="deck">{html.escape(c["deck"])}</p>' if c["deck"] else ""
        return (f'<div class="side"><div class="when">{label} · {html.escape(c["day"])} · '
                f'{html.escape(", ".join(c["papers"]))}</div>'
                f'<h4>{html.escape(c["headline"])}</h4>{deck}<p class="open">{html.escape(c["opening"])}</p>{others}</div>')

    radios = lambda n: "".join(
        f'<label class="opt"><input type="radio" name="p{n}" value="{k}"><span><b>{html.escape(t)}</b>'
        f'<small>{html.escape(h)}</small></span></label>' for k, t, h in LABELS)
    cards = "".join(
        f'<section class="pair" id="pair{p["n"]}"><div class="ph">Pair {p["n"]} · {p["gap"]} day(s) apart</div>'
        f'<div class="sides">{side(p["earlier"], "Earlier")}{side(p["later"], "Later")}</div>'
        f'<div class="labels">{radios(p["n"])}</div>'
        f'<input class="note" data-n="{p["n"]}" placeholder="Optional note: the anchor, or why you hesitated">'
        f'</section>' for p in pairs)
    return PAGE.replace("@@NAME@@", html.escape(name)).replace("@@N@@", str(len(pairs))) \
        .replace("@@CODEBOOK@@", CODEBOOK).replace("@@CARDS@@", cards)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-day links · @@NAME@@</title>
<style>
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a65;--line:#e2e0d8;--acc:#2f5d8a;--sel:#e8f0f8}
@media (prefers-color-scheme:dark){:root{--bg:#18191b;--card:#222326;--ink:#ebeae6;--mute:#a2a19b;--line:#34353a;--acc:#8db4dc;--sel:#243344}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;flex:1}
button{font:inherit;border:1px solid var(--acc);background:var(--card);color:var(--acc);border-radius:8px;padding:6px 12px;cursor:pointer}
button.primary{background:var(--acc);color:var(--bg)}
#progress{color:var(--mute);font-size:14px}
main{max-width:1100px;margin:0 auto;padding:16px}
.intro{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:16px}
.pair{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:0 0 16px}
.pair.done{border-color:var(--acc)}
.ph{font-weight:600;color:var(--mute);font-size:13px;margin-bottom:8px}
.sides{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:720px){.sides{grid-template-columns:1fr}}
.side{border-left:3px solid var(--line);padding-left:10px}
.when{font-size:12px;color:var(--mute)}
.side h4{margin:4px 0;font-size:16px}.deck{margin:0 0 4px;font-style:italic;color:var(--mute)}
.open{margin:4px 0;font-size:14px}.others{font-size:13px;color:var(--mute)}
.labels{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.opt{flex:1 1 180px;display:flex;gap:6px;align-items:flex-start;border:1px solid var(--line);border-radius:8px;padding:6px 8px;cursor:pointer}
.opt:has(input:checked){background:var(--sel);border-color:var(--acc)}
.opt small{display:block;color:var(--mute);font-size:12px;line-height:1.3}
.note{width:100%;margin-top:8px;font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
#rules{position:fixed;top:0;right:0;bottom:0;width:min(560px,100%);background:var(--card);border-left:1px solid var(--line);overflow:auto;padding:16px 20px;transform:translateX(100%);transition:transform .2s;z-index:10;box-shadow:-8px 0 24px rgba(0,0,0,.15)}
#rules.open{transform:none}#rules h3{margin:16px 0 6px;font-size:15px}
.src{color:var(--mute);font-size:12px}
</style></head><body>
<header><h1>Cross-day links · sheet @@NAME@@</h1><span id="progress"></span>
<button onclick="toggleRules()">Rules</button><button class="primary" onclick="save()">Save labels</button></header>
<main>
<div class="intro"><p><b>@@N@@ pairs.</b> For each pair, read both sides and pick one label. The
<b>Rules</b> button (top right) opens the full definitions at any point. Your answers are kept in this
browser as you go; press <b>Save labels</b> at the end (and any time you stop) — it downloads
<code>labels_@@NAME@@.json</code>. Move that file to <code>~/news-intel/review/threads/</code>.</p>
<p class="src">Nothing on this page says which pairs the machine linked. Please don't look up scores
before you finish.</p></div>
@@CARDS@@
</main>
<aside id="rules"><button onclick="toggleRules()">Close</button>@@CODEBOOK@@</aside>
<script>
const NAME="@@NAME@@", N=@@N@@, STORE="tracksheet_"+NAME;
function load(){try{return JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){return {}}}
function keep(s){try{localStorage.setItem(STORE,JSON.stringify(s))}catch(e){}}
let state=load();
function collect(){
  const out={};
  for(let n=1;n<=N;n++){
    const r=document.querySelector(`input[name=p${n}]:checked`);
    const note=document.querySelector(`.note[data-n="${n}"]`).value.trim();
    if(r||note) out[n]={label:r?r.value:null,note:note};
  }
  return out;
}
function refresh(){
  state=collect(); keep(state);
  let done=0;
  for(let n=1;n<=N;n++){const ok=state[n]&&state[n].label; if(ok)done++;
    document.getElementById("pair"+n).classList.toggle("done",!!ok);}
  document.getElementById("progress").textContent=`${done} / ${N} labelled`;
}
function restore(){
  for(const [n,v] of Object.entries(state)){
    if(v.label){const r=document.querySelector(`input[name=p${n}][value=${v.label}]`); if(r) r.checked=true;}
    const t=document.querySelector(`.note[data-n="${n}"]`); if(t&&v.note) t.value=v.note;
  }
}
function save(){
  refresh();
  const blob=new Blob([JSON.stringify({sheet:NAME,saved_at:new Date().toISOString(),labels:state},null,1)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=`labels_${NAME}.json`;
  document.body.appendChild(a); a.click(); a.remove();
}
function toggleRules(){document.getElementById("rules").classList.toggle("open")}
document.addEventListener("change",refresh); document.addEventListener("input",refresh);
restore(); refresh();
</script></body></html>
"""


def cmd_score(args):
    key_path = KEY_DIR / f"sheet_{args.name}_key.json"
    labels_path = Path(args.labels or REVIEW_DIR / f"labels_{args.name}.json")
    for p in (key_path, labels_path):
        if not p.exists():
            sys.exit(f"missing {p}")
    key = json.loads(key_path.read_text())
    lab = json.loads(labels_path.read_text())["labels"]
    if json.loads(labels_path.read_text()).get("sheet") != args.name:
        sys.exit("labels file belongs to another sheet")
    missing = [n for n in key["pairs"] if not (lab.get(n) or {}).get("label")]
    print(f"sheet {args.name} · codebook {key['meta']['codebook']} · seed {key['meta']['seed']} · "
          f"{len(key['pairs']) - len(missing)} / {len(key['pairs'])} labelled"
          + (f" · UNLABELLED: {', '.join(missing)}" if missing else ""))
    names = {k: t for k, t, _ in LABELS}
    for role, line in (("made", PASS_PRECISION), ("rejected", TOO_STRICT)):
        rows = [(n, v) for n, v in key["pairs"].items() if v["role"] == role and (lab.get(n) or {}).get("label")]
        counts = {k: sum(1 for n, _ in rows if lab[n]["label"] == k) for k, _, _ in LABELS}
        judged = len(rows) - counts["cant_tell"]
        hit = counts["continuing"] + counts["followup"]
        share = hit / judged if judged else 0
        print(f"\n{role.upper()} ({'links the machine made' if role == 'made' else 'best rejected, just below the line'})"
              f": {len(rows)} labelled, {counts['cant_tell']} can't tell → {judged} judged")
        print("  " + " · ".join(f"{names[k]} {counts[k]}" for k, _, _ in LABELS))
        if role == "made":
            verdict = "PASS" if share >= line else "FAIL (line too loose)"
            print(f"  continuing + follow-up {hit} / {judged} = {share:.0%}  →  precision line ≥ {line:.0%}: {verdict}")
        else:
            verdict = "TOO STRICT (real continuations being missed)" if share >= line else "line holds"
            pool = key["meta"]["pool_rejected_in_band"]
            print(f"  continuing + follow-up {hit} / {judged} = {share:.0%}  →  too-strict line ≥ {line:.0%}: {verdict}")
            print(f"  rough size of what is missed: {share:.0%} of the {pool} rejected groups in the band ≈ {share * pool:.0f}")
        by_gap = {}
        for n, v in rows:
            if lab[n]["label"] != "cant_tell":
                g = "1-2" if v["gap_days"] <= 2 else "3-4" if v["gap_days"] <= 4 else "5-6" if v["gap_days"] <= 6 else "7"
                a, b = by_gap.get(g, (0, 0))
                by_gap[g] = (a + (lab[n]["label"] in LINKED), b + 1)
        print("  by gap (days): " + "  ".join(f"{g}: {a}/{b}" for g, (a, b) in sorted(by_gap.items())))
    notes = [(n, lab[n]["note"]) for n in sorted(lab, key=int) if (lab.get(n) or {}).get("note")]
    if notes:
        print(f"\nnotes: {len(notes)} pair(s) have a note (read with the key after scoring)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="draw the sample and write the blind sheet + hidden key")
    m.add_argument("--name", default="t01")
    m.add_argument("--seed", type=int, default=2810)
    m.add_argument("--n", type=int, default=20, help="pairs per side (made / rejected)")
    s = sub.add_parser("score", help="apply the pass lines to Kunaal's labels")
    s.add_argument("--name", default="t01")
    s.add_argument("--labels", help="path to labels_<name>.json (default review/threads/)")
    args = ap.parse_args()
    {"make": cmd_make, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
