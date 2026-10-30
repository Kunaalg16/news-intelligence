"""p3_dev_tracksheet.py — Phase 3 dev tool · blind checks of stage-2 cross-day links.
  v0.1 28 Oct 2026 (B·3)   make / score: sheet t01 (machine's line, made vs rejected)
  v0.2 30 Oct 2026 (B·4)   draw / agree: the cross-day JUDGE agreement test (codebook v0.2)

WHAT IT DOES
  draw    (v0.2) The agreement test's sheet, as decided 29 Oct (drafts/2026-10-29_B4_cross_day_judge.md §4):
          POOL  = the pairs the judge will face every day: for each later day-group in stage 2 (t0.2),
                  its best earlier candidate with average ≥ 0.70 — the parent link if it has one,
                  otherwise its rank-1 rejected candidate in [0.70, 0.810);
                  minus pairs where either side is made only of `notice` / `sponsored` texts (tag v1.3);
                  minus the 40 pairs of sheet t01 (already labelled; 7 of them are codebook examples).
          DRAW  = simple random sample, seeded, of --n pairs (default 100). No per-date cap unless
                  --per-date is given (a cap is a pre-result amendment Kunaal chooses after a --dry run).
          --dry prints the pool, the per-date spread of the draw and made/band counts; writes nothing.
          Writes:
            review/threads/sheet_<name>.html        blind labelling page (no scores, no roles)
            data/threads/sheet_<name>_key.json      hidden answer key (role, scores) — do NOT open before agree
            data/threads/inputs_<name>.jsonl        the judge's input per pair (also what the sheet's
                                                    "Full text" fold-out shows, word for word)
          Refuses to overwrite an existing key (a re-draw after seeing pairs would break blindness).
  agree   (v0.2) Applies the pass rule written before any label (29 Oct):
            Kunaal's "can't tell" pairs are left out; more than 10 → TEST VOID (cards too thin).
            Gate: Cohen's kappa ≥ 0.70 on LINKED (continuing + follow-up) vs NOT (subject + unrelated),
            Kunaal vs the gating judge (Haiku 5.5). Optional --diag file (Opus) decides the branch on a fail.
            Prints kappa, raw agreement, 2×2, a bootstrap 95% range, the 4×4 table, made/band side note.
  make    (v0.1, kept for the record) t01-style sheet: N made + N rejected, tversion t0.1.
  score   (v0.1, unchanged) t01 pass lines; `score --name t01` must reproduce t01's numbers.

WHY
  Stage 2 links by a similarity line; sheet t01 showed the line alone is not enough (both pre-set lines
  failed). A Haiku judge applying codebook v0.2 types each candidate link. It may link only after it
  agrees with Kunaal's blind labels (kappa ≥ 0.70) on a fresh, representative set.

INFORMATION PARITY (decision C, 29 Oct)
  The judge reads, per side: date, papers, headline, deck, the longest article's body up to 1,200 words,
  and the other articles' headlines. ONE function (side_input) builds this; the judge text and the sheet's
  fold-out are both made from it, so they cannot drift apart. The card shows the first 70 words of it.

DATA
  reads   data/intel.db, data/news.db (read-only); data/threads/sheet_t01_key.json (exclusion)
  writes  review/threads/, data/threads/ (files above). Kunaal's labels: the sheet downloads
          labels_<name>.json → move it to review/threads/ (hand-made: back up). Never upload it to a chat.

HOW TO RUN (from ~/news-intel, pipeline environment)
  .venv/bin/python src/p3_dev_tracksheet.py draw  --name x01 --dry          (counts only)
  .venv/bin/python src/p3_dev_tracksheet.py draw  --name x01                (writes sheet, key, inputs)
  .venv/bin/python src/p3_dev_tracksheet.py agree --name x01 --judge data/threads/judge_x01_haiku.json \
                                                   [--diag data/threads/judge_x01_opus.json]
  .venv/bin/python src/p3_dev_tracksheet.py score --name t01                (v0.1 regression check)
Standard library only.
"""

import argparse
import html
import json
import random
import re
import sqlite3
import sys
from datetime import date as Date, datetime, timezone
from pathlib import Path

NEWS_DB, INTEL_DB = Path("data/news.db"), Path("data/intel.db")
REVIEW_DIR, KEY_DIR = Path("review/threads"), Path("data/threads")
GVERSION = "g0.1"
RUN_ID = "exam-v1"
LINK_LINE = 0.810
BAND_LOW = 0.70          # candidates the judge reads: average ≥ 0.70 (B·3 / B·4)
OPENING_WORDS = 70       # words of body on the card (t01 used 70)

# ---- v0.2: judge agreement test (B·4, decided 29 Oct) -------------------------------------------
TVERSION = "t0.2"                      # stage 2 version the test draws from
TAG_MODEL, TAG_PROMPT = "claude-haiku-5-5", "v1.3"
EXCLUDE_KINDS = {"notice", "sponsored"}   # a side made only of these kinds is not news (as t0.2)
JUDGE_WORDS = 1200                     # body words per article the judge reads (= same-day judge)
EXCLUDE_SHEETS = ("t01",)              # sheets whose pairs never enter a new test
KAPPA_LINE = 0.70
MAX_CANT_TELL = 10
DRAW_SEED = 3010
CODEBOOK_V2_VERSION = "cross-day v0.2 (approved 29 Oct 2026, B·4)"

# ---- v0.1: t01 (kept so `score --name t01` reproduces) -------------------------------------------
T01_TVERSION = "t0.1"
PASS_PRECISION = 0.90    # 18 of 20
TOO_STRICT = 0.40        # 8 of 20
CODEBOOK_VERSION = "cross-day v0.1 (draft, 28 Oct, B·3)"

LABELS = [  # (key, text on the sheet, one-line reminder under it) — v0.1 wording (t01)
    ("continuing", "Same story continuing",
     "The same happening, or the next stage of the same bounded episode."),
    ("followup", "Follow-up",
     "A NEW happening on the later day, taken in response to / caused by the earlier one."),
    ("subject", "Same subject only",
     "Same theme, but you cannot name one dated happening both stem from."),
    ("unrelated", "Unrelated", "No shared subject."),
    ("cant_tell", "Can't tell", "Text cut off or too little to judge (left out of the count)."),
]
LABELS_V2 = [  # v0.2 wording (codebook v0.2)
    ("continuing", "1 · Same story continuing",
     "The same happening or activity, still going or one stage further (run-up and commentary count)."),
    ("followup", "2 · Follow-up",
     "A new happening that exists because of the earlier one (someone else acting, reacting, affected)."),
    ("subject", "3 · Same subject only",
     "A shared theme, but no direct line from the earlier happening or activity."),
    ("unrelated", "4 · Unrelated", "No shared subject."),
    ("cant_tell", "5 · Can't tell", "The text you have (card + full text) is too thin to judge. Left out."),
]
JUDGE_LABELS = ("continuing", "followup", "subject", "unrelated")
LINKED = {"continuing", "followup"}

# =================================================================================================
# Codebook v0.2 — the judge's instructions (verbatim, approved 29 Oct). The labelling sheet shows the
# same text plus READER_LINE (the reader line is for the human only; decision 4, 29 Oct).
# =================================================================================================
READER_LINE = ("Use only what the two stories say (the card and its full text) plus ordinary general "
               "knowledge; don't split a story into strands that only deep background would reveal.")

CODEBOOK_V2 = """**Task.** You see two stories from different days, 1 to 7 days apart: the earlier one and the later one. Decide how the later one relates to the earlier one, using exactly one of four labels.

**Ideas.** A *happening* is a specific dated occurrence: a decision, an announcement, a ruling, a release of figures, an incident, an arrest, a protest action, a speech, a match, a vote, a deal, a filing. An *activity* is a connected set of happenings with one purpose, by the same actors, at one place or route, over consecutive days: a sit-in, a march, a strike, a match series, a tournament's knockout stage, an IPO from price band to listing, one trial's hearings, one round of talks. A *story* is a happening or an activity plus what directly stems from it. A *subject* is a broader theme: a policy, a programme, a long process, a movement, a market, an organisation, a person.

**Labels.**
1. **Same story continuing** — the later story reports the same happening or activity, still unfolding or one stage further. This includes its run-up (a lockdown before a called protest, a match preview, a permission refused for the protest) and commentary on that same happening (an editorial days later on the ruling).
2. **Follow-up** — a new happening that exists because of the earlier one: someone else acting, reacting or being affected (an FIR or court petition over detentions, a government reply to a rejection, a market move after an announcement, a party answering another's specific statement).
3. **Same subject only** — a shared theme, but no direct line from the earlier happening or activity.
4. **Unrelated** — no shared subject.

**The test.** Name the earlier anchor: the happening or activity, with its date. If the later story is that anchor still going, it is 1. If it is a new happening caused by the anchor, it is 2. If neither, it is 3. **If you hesitate between linked (1 or 2) and 3, choose 3.**

**Rules by type.**
- Protests: same organisers, same cause, same place or route, on consecutive days → 1. Police, court or political consequences → 2. The same cause in another city or by other organisers → 3, unless one explicitly reacts to a specific happening in the other.
- Sports: one match, a series between the same two sides, or one tournament's knockout run → 1. League matches between different teams → 3.
- Legal and criminal: one case, from incident through arrest, investigation and hearings to verdict → 1. A different case on the same issue → 3.
- Elections: one polling event, its results and government formation → 1 or 2. Parallel rallies or manifestos of different parties → 3, unless one replies to the other.
- Companies and markets: one deal, IPO, results release or regulator decision, with its reactions → 1 or 2. Another company's similar deal, or a market trend with no single trigger → 3.
- Policy and regulation: one decision through its stages (proposed, notified, in force) plus its reactions and analysis → 1 or 2. Another decision under the same policy → 3.
- Disasters and accidents: the event, toll, rescue, cause finding and compensation → 1 or 2.
- Meetings and talks: one meeting with its preparations and outcome → 1. Each side's response to that outcome → 2. A later round that isn't announced as an outcome of this one → 3.

**Worked examples.**
- A student group's march toward Jantar Mantar on 11 Oct → the same group renewing the march on 12 Oct → 1 (same activity).
- Police seal central Delhi ahead of a called youth protest (Friday) → the protest itself on Saturday → 1 (the run-up counts).
- The Supreme Court issues contempt notices over Metro curbs on a protest day → the Metro operator's reply in court three days later → 2.
- An opposition alliance's march in Delhi (8 Oct) → a youth solidarity protest in Mumbai (11 Oct) on the same cause → 3 (other organisers, other city).
- One T20 match of a bilateral series → the next match of that series → 1. Two league matches of an archery league between different teams → 3.
- A bank weighs legal options after a Supreme Court ruling → an editorial on that same ruling five days later → 1 (commentary on the same happening).

**Output:** `label` (one of the four) · `anchor` (a few words with a date; empty for labels 3 and 4) · `reason` (one sentence)."""


def md_html(md):
    """Small markdown → HTML for the codebook: paragraphs, **bold**, *italic*, `code`, '- ' and '1.' lists."""
    out = []
    for para in md.strip().split("\n\n"):
        t = html.escape(para.strip())
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\*(.+?)\*", r"<i>\1</i>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        lines = t.split("\n")
        if len(lines) > 1 and all(re.match(r"^(- |[0-9]\. )", l.strip()) for l in lines[1:]):
            tag = "ol" if lines[1].strip()[0].isdigit() else "ul"
            bullet = re.compile(r"^(- |[0-9]\. )")
            items = "".join("<li>" + bullet.sub("", l.strip()) + "</li>" for l in lines[1:])
            out.append(f"<p>{lines[0]}</p><{tag}>{items}</{tag}>")
        else:
            out.append(f"<p>{'<br>'.join(lines)}</p>")
    return "".join(out)


# =================================================================================================
# Shared helpers
# =================================================================================================
def open_ro(path):
    if not path.exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def weekday(d):
    return Date.fromisoformat(d).strftime("%a %d %b %Y")


_deck = None


def has_deck(news):
    global _deck
    if _deck is None:
        _deck = any(r[1] == "deck" for r in news.execute("PRAGMA table_info(placements)"))
    return _deck


def group_articles(news, intel, gid, gdate):
    """All articles of a day-group: headline, body, words, papers, deck; longest first."""
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
            arts.append({"hash": h, "headline": (row[0] or "(no headline)").strip(), "body": row[1] or "",
                         "words": row[2] or 0, "papers": pubs, "deck": (deck[0] if deck else "")})
    arts.sort(key=lambda a: (-a["words"], a["headline"]))
    return arts


def group_card(news, intel, gid, gdate):
    """v0.1 card (t01): the group's longest article + the other members' headlines."""
    arts = group_articles(news, intel, gid, gdate)
    lead = arts[0]
    words = lead["body"].split()
    opening = " ".join(words[:OPENING_WORDS]) + (" …" if len(words) > OPENING_WORDS else "")
    return {"date": gdate, "day": weekday(gdate), "headline": lead["headline"], "deck": lead["deck"],
            "opening": opening, "papers": lead["papers"], "words": lead["words"],
            "others": [f'{a["headline"]} ({", ".join(a["papers"])})' for a in arts[1:]]}


# =================================================================================================
# v0.2 · information parity: ONE builder for the judge's input and the sheet's fold-out
# =================================================================================================
def side_input(news, intel, gid, gdate):
    """What the judge reads for one side (and what the sheet shows, word for word)."""
    arts = group_articles(news, intel, gid, gdate)
    if not arts:
        return None
    lead = arts[0]
    words = lead["body"].split()
    return {"group_id": gid, "date": gdate, "day": weekday(gdate), "papers": lead["papers"],
            "headline": lead["headline"], "deck": lead["deck"], "words_total": len(words),
            "body": " ".join(words[:JUDGE_WORDS]), "cut": len(words) > JUDGE_WORDS,
            "others": [f'{a["headline"]} ({", ".join(a["papers"])})' for a in arts[1:]]}


def side_text(s, role):
    """The exact text block the judge gets for one side. The sheet's fold-out prints this string."""
    lines = [f"{role.upper()} STORY · {s['day']} · {', '.join(s['papers'])}",
             f"Headline: {s['headline']}"]
    if s["deck"]:
        lines.append(f"Deck: {s['deck']}")
    if s["cut"]:
        size = f"first {JUDGE_WORDS} of {s['words_total']} words"
    else:
        size = f"{s['words_total']} words"
    lines.append(f"Text ({size}):")
    lines.append(s["body"])
    if s["others"]:
        lines.append("Other articles in this story (headlines only):")
        lines += [f"- {o}" for o in s["others"]]
    return "\n".join(lines)


def pair_text(gap, earlier, later):
    """The judge's whole user message for one pair."""
    return (f"The later story is {gap} day(s) after the earlier one.\n\n"
            f"{side_text(earlier, 'earlier')}\n\n{side_text(later, 'later')}")


def card_from_side(s):
    words = s["body"].split()
    opening = " ".join(words[:OPENING_WORDS]) + (" …" if len(words) > OPENING_WORDS or s["cut"] else "")
    return {"day": s["day"], "papers": s["papers"], "headline": s["headline"], "deck": s["deck"],
            "opening": opening, "words": s["words_total"], "others": s["others"]}


def excluded_kind_groups(news, intel, gids):
    """Day-groups whose every member is tagged notice/sponsored (tag v1.3): not news, as in t0.2."""
    kind = {h: k for h, k in news.execute(
        "SELECT text_hash, kind FROM tags WHERE model=? AND prompt_version=?", (TAG_MODEL, TAG_PROMPT))}
    out = set()
    for gid in gids:
        ks = [kind.get(h) for (h,) in intel.execute(
            "SELECT text_hash FROM members WHERE version=? AND group_id=?", (GVERSION, gid))]
        if ks and all(k in EXCLUDE_KINDS for k in ks):
            out.add(gid)
    return out


def old_sheet_pairs():
    """(date, group_id, earlier_date, earlier_group_id) of every pair on an excluded sheet."""
    seen = set()
    for name in EXCLUDE_SHEETS:
        p = KEY_DIR / f"sheet_{name}_key.json"
        if not p.exists():
            sys.exit(f"missing {p}: needed to keep sheet {name}'s pairs out of the new test.")
        for v in json.loads(p.read_text())["pairs"].values():
            seen.add((v["date"], v["group_id"], v["earlier_date"], v["earlier_group_id"]))
    return seen


def build_pool(news, intel):
    cols = ("l.date, l.group_id, l.earlier_date, l.earlier_group_id, l.gap_days, l.avg_score, "
            "l.best_score, l.centroid_score")
    made = intel.execute(
        f"SELECT {cols} FROM links l WHERE l.gversion=? AND l.tversion=? AND l.role='parent'",
        (GVERSION, TVERSION)).fetchall()
    band = intel.execute(
        f"SELECT {cols} FROM links l WHERE l.gversion=? AND l.tversion=? AND l.role='below' AND l.rank=1 "
        "AND l.avg_score >= ? AND l.avg_score < ? AND NOT EXISTS (SELECT 1 FROM links p WHERE "
        "p.gversion=l.gversion AND p.tversion=l.tversion AND p.date=l.date AND p.group_id=l.group_id "
        "AND p.role='parent')", (GVERSION, TVERSION, BAND_LOW, LINK_LINE)).fetchall()
    if not made and not band:
        have = intel.execute("SELECT gversion, tversion, role, COUNT(*) FROM links GROUP BY 1, 2, 3").fetchall()
        sys.exit(f"no stage-2 candidates for {GVERSION}/{TVERSION}. Present: {have}")
    rows = [("made", r) for r in made] + [("band", r) for r in band]
    counts = {"made links": len(made), f"band [{BAND_LOW}, {LINK_LINE})": len(band)}
    junk = excluded_kind_groups(news, intel, {r[1] for _, r in rows} | {r[3] for _, r in rows})
    old = old_sheet_pairs()
    pool, n_junk, n_old = [], 0, 0
    for role, r in rows:
        if r[1] in junk or r[3] in junk:
            n_junk += 1
            continue
        if (r[0], r[1], r[2], r[3]) in old:
            n_old += 1
            continue
        pool.append((role, r))
    pool.sort(key=lambda x: (x[1][0], x[1][1]))           # deterministic order before the seeded draw
    counts["notice/sponsored side"] = n_junk
    counts["on an earlier sheet (t01)"] = n_old
    counts["pool"] = len(pool)
    counts["pool made / band"] = f"{sum(1 for x in pool if x[0] == 'made')} / {sum(1 for x in pool if x[0] == 'band')}"
    return pool, counts


def capped_sample(pool, n, per_date, rng):
    """Seeded shuffle, then take in order; at most per_date pairs per later date (only if per_date given)."""
    order = pool[:]
    rng.shuffle(order)
    if not per_date:
        return order[:n]
    got, out = {}, []
    for role, r in order:
        if got.get(r[0], 0) >= per_date:
            continue
        got[r[0]] = got.get(r[0], 0) + 1
        out.append((role, r))
        if len(out) == n:
            break
    return out


def cmd_draw(args):
    key_path = KEY_DIR / f"sheet_{args.name}_key.json"
    sheet_path = REVIEW_DIR / f"sheet_{args.name}.html"
    inputs_path = KEY_DIR / f"inputs_{args.name}.jsonl"
    if args.name in EXCLUDE_SHEETS:
        sys.exit(f"--name {args.name} is a closed sheet; pick a new name (e.g. x01).")
    if key_path.exists() and not args.dry:
        sys.exit(f"{key_path} exists: refusing to re-draw (blindness). Use a new --name for a new sheet.")
    news, intel = open_ro(NEWS_DB), open_ro(INTEL_DB)
    pool, counts = build_pool(news, intel)
    print("pool: " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    if len(pool) < args.n:
        sys.exit(f"STOP: pool {len(pool)} < {args.n} pairs wanted; nothing written.")
    rng = random.Random(args.seed)
    picks = capped_sample(pool, args.n, args.per_date, rng)
    if len(picks) < args.n:
        sys.exit(f"STOP: only {len(picks)} pairs under --per-date {args.per_date}; nothing written.")

    pool_dates, draw_dates, groups = {}, {}, {}
    for _, r in pool:
        pool_dates[r[0]] = pool_dates.get(r[0], 0) + 1
    for _, r in picks:
        draw_dates[r[0]] = draw_dates.get(r[0], 0) + 1
        for g in (r[1], r[3]):
            groups[g] = groups.get(g, 0) + 1
    n_made = sum(1 for role, _ in picks if role == "made")
    print(f"drawn {len(picks)} (seed {args.seed}{', per-date cap ' + str(args.per_date) if args.per_date else ''}) · "
          f"made {n_made} / band {len(picks) - n_made} · later dates {len(draw_dates)} · "
          f"max pairs per day-group {max(groups.values())}")
    print("per later date (drawn / pool): " + "  ".join(
        f"{d[5:]}:{draw_dates.get(d, 0)}/{pool_dates[d]}" for d in sorted(pool_dates)))
    top = sorted(draw_dates.items(), key=lambda x: -x[1])[:3]
    print(f"densest dates in the draw: {', '.join(f'{d[5:]} {k}' for d, k in top)} "
          f"= {sum(k for _, k in top)} of {len(picks)} ({sum(k for _, k in top) / len(picks):.0%})")
    if args.dry:
        print("dry run: nothing written")
        return

    pairs, key, inputs = [], {}, []
    for k, (role, (d, gid, ed, egid, gap, avg, best, cen)) in enumerate(picks, start=1):
        pid = f"X{k:03d}"
        e, l = side_input(news, intel, egid, ed), side_input(news, intel, gid, d)
        if e is None or l is None:
            sys.exit(f"STOP: pair {pid} has a side with no articles ({egid} / {gid}); nothing written.")
        pairs.append({"id": pid, "gap": gap, "earlier": e, "later": l})
        key[pid] = {"role": role, "group_id": gid, "date": d, "earlier_group_id": egid, "earlier_date": ed,
                    "gap_days": gap, "avg_score": avg, "best_score": best, "centroid_score": cen}
        inputs.append({"id": pid, "gap_days": gap, "text": pair_text(gap, e, l)})

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    meta = {"sheet": args.name, "test": "cross-day judge agreement", "seed": args.seed, "n": args.n,
            "per_date": args.per_date, "gversion": GVERSION, "tversion": TVERSION, "band_low": BAND_LOW,
            "judge_words": JUDGE_WORDS, "excluded_sheets": list(EXCLUDE_SHEETS), "counts": counts,
            "codebook": CODEBOOK_V2_VERSION, "design": "drafts/2026-10-29_B4_cross_day_judge.md §4",
            "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    key_path.write_text(json.dumps({"meta": meta, "pairs": key}, indent=1))
    with open(inputs_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"meta": {"sheet": args.name, "codebook": CODEBOOK_V2_VERSION,
                                     "judge_words": JUDGE_WORDS}}) + "\n")
        for x in inputs:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    sheet_path.write_text(render_v2(args.name, pairs), encoding="utf-8")
    print(f"sheet  {sheet_path}   ({len(pairs)} pairs; no scores or roles inside)")
    print(f"key    {key_path}   (do not open before agree)")
    print(f"inputs {inputs_path}   (judge input = the sheet's full-text fold-outs)")


def render_v2(name, pairs):
    def side(s, label):
        c = card_from_side(s)
        others = ""
        if c["others"]:
            items = "".join(f"<li>{html.escape(o)}</li>" for o in c["others"])
            others = (f'<details class="others"><summary>+ {len(c["others"])} other article(s) in this story'
                      f'</summary><ul>{items}</ul></details>')
        deck = f'<p class="deck">{html.escape(c["deck"])}</p>' if c["deck"] else ""
        full = (f'<details class="full"><summary>Full text (exactly what the judge reads · '
                f'{min(c["words"], JUDGE_WORDS)} of {c["words"]} words)</summary>'
                f'<div class="ft">{html.escape(side_text(s, label))}</div></details>')
        return (f'<div class="side"><div class="when">{label} · {html.escape(c["day"])} · '
                f'{html.escape(", ".join(c["papers"]))} · {c["words"]} words</div>'
                f'<h4>{html.escape(c["headline"])}</h4>{deck}<p class="open">{html.escape(c["opening"])}</p>'
                f'{others}{full}</div>')

    radios = lambda pid: "".join(
        f'<label class="opt"><input type="radio" name="{pid}" value="{k}"><span><b>{html.escape(t)}</b>'
        f'<small>{html.escape(h)}</small></span></label>' for k, t, h in LABELS_V2)
    cards = "".join(
        f'<section class="pair" id="{p["id"]}"><div class="ph">{p["id"]} · {p["gap"]} day(s) apart</div>'
        f'<div class="sides">{side(p["earlier"], "Earlier")}{side(p["later"], "Later")}</div>'
        f'<div class="labels">{radios(p["id"])}</div>'
        f'<input class="note" data-id="{p["id"]}" placeholder="Optional: the anchor, or why you hesitated">'
        f'</section>' for p in pairs)
    ids = json.dumps([p["id"] for p in pairs])
    rules = (f'<h3>Before you start</h3><p><b>{html.escape(READER_LINE)}</b></p>'
             f'<h3>Codebook</h3>{md_html(CODEBOOK_V2)}<p class="src">{html.escape(CODEBOOK_V2_VERSION)}. '
             f'The judge gets this codebook without the "Before you start" line.</p>')
    return (PAGE_V2.replace("@@NAME@@", html.escape(name)).replace("@@N@@", str(len(pairs)))
            .replace("@@READER@@", html.escape(READER_LINE)).replace("@@RULES@@", rules)
            .replace("@@CBV@@", html.escape(CODEBOOK_V2_VERSION)).replace("@@IDS@@", ids)
            .replace("@@CARDS@@", cards))


PAGE_V2 = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-day judge test · @@NAME@@</title>
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
.side{border-left:3px solid var(--line);padding-left:10px;min-width:0}
.when{font-size:12px;color:var(--mute)}
.side h4{margin:4px 0;font-size:16px}.deck{margin:0 0 4px;font-style:italic;color:var(--mute)}
.open{margin:4px 0;font-size:14px}.others{font-size:13px;color:var(--mute)}
details.full{font-size:13px;margin-top:6px}details.full summary{color:var(--acc);cursor:pointer}
.ft{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.45;margin-top:6px;padding:8px;background:var(--bg);border-radius:6px;max-height:420px;overflow:auto}
.labels{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.opt{flex:1 1 180px;display:flex;gap:6px;align-items:flex-start;border:1px solid var(--line);border-radius:8px;padding:6px 8px;cursor:pointer}
.opt:has(input:checked){background:var(--sel);border-color:var(--acc)}
.opt small{display:block;color:var(--mute);font-size:12px;line-height:1.3}
.note{width:100%;margin-top:8px;font:inherit;font-size:14px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
#rules{position:fixed;top:0;right:0;bottom:0;width:min(600px,100%);background:var(--card);border-left:1px solid var(--line);overflow:auto;padding:16px 20px;transform:translateX(100%);transition:transform .2s;z-index:10;box-shadow:-8px 0 24px rgba(0,0,0,.15)}
#rules.open{transform:none}#rules h3{margin:16px 0 6px;font-size:15px}
.src{color:var(--mute);font-size:12px}
</style></head><body>
<header><h1>Cross-day judge test · sheet @@NAME@@</h1><span id="progress"></span>
<button onclick="toggleRules()">Rules</button><button onclick="nextUn()">Next unlabelled</button><button class="primary" onclick="save()">Save labels</button></header>
<main>
<div class="intro"><p><b>@@N@@ pairs.</b> Each pair: an <b>earlier</b> story (left) and a <b>later</b> one (right),
1 to 7 days apart. Pick how the later one relates to the earlier one. Open <b>Rules</b> for the codebook
(@@CBV@@) at any time.</p>
<p><b>@@READER@@</b></p>
<p>Each side shows a card. Under it, <b>Full text</b> holds exactly what the judge reads (up to 1,200 words
per article): open it whenever the card leaves you unsure. Answers are kept in this browser as you go;
press <b>Save labels</b> when you stop (it downloads <code>labels_@@NAME@@.json</code>) and move the file to
<code>~/news-intel/review/threads/</code>. Never upload it to a chat.</p>
<p class="src">Nothing on this page says which pairs the machine linked or what any model said.</p></div>
@@CARDS@@
</main>
<aside id="rules"><button onclick="toggleRules()">Close</button>@@RULES@@</aside>
<script>
const NAME="@@NAME@@", IDS=@@IDS@@, STORE="tracksheet_"+NAME;
function load(){try{return JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){return {}}}
function keep(s){try{localStorage.setItem(STORE,JSON.stringify(s))}catch(e){}}
let state=load();
function collect(){
  const out={};
  for(const id of IDS){
    const r=document.querySelector(`input[name="${id}"]:checked`);
    const note=document.querySelector(`.note[data-id="${id}"]`).value.trim();
    if(r||note) out[id]={label:r?r.value:null,note:note};
  }
  return out;
}
function refresh(){
  state=collect(); keep(state);
  let done=0;
  for(const id of IDS){const ok=state[id]&&state[id].label; if(ok)done++;
    document.getElementById(id).classList.toggle("done",!!ok);}
  document.getElementById("progress").textContent=`${done} / ${IDS.length} labelled`;
}
function restore(){
  for(const [id,v] of Object.entries(state)){
    if(v.label){const r=document.querySelector(`input[name="${id}"][value="${v.label}"]`); if(r) r.checked=true;}
    const t=document.querySelector(`.note[data-id="${id}"]`); if(t&&v.note) t.value=v.note;
  }
}
function nextUn(){for(const id of IDS){if(!(state[id]&&state[id].label)){document.getElementById(id).scrollIntoView({behavior:"smooth"});return}}}
function save(){
  refresh();
  const blob=new Blob([JSON.stringify({sheet:NAME,codebook:"@@CBV@@",saved_at:new Date().toISOString(),labels:state},null,1)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=`labels_${NAME}.json`;
  document.body.appendChild(a); a.click(); a.remove();
}
function toggleRules(){document.getElementById("rules").classList.toggle("open")}
document.addEventListener("change",refresh); document.addEventListener("input",refresh);
restore(); refresh();
</script></body></html>
"""


# =================================================================================================
# v0.2 · agree: the pre-registered pass rule
# =================================================================================================
def kappa(pairs):
    """Cohen's kappa for two raters on binary labels; pairs = [(a, b), ...] with a, b in {0, 1}."""
    n = len(pairs)
    if n == 0:
        return None, None
    po = sum(1 for a, b in pairs if a == b) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return ((po - pe) / (1 - pe) if pe < 1 else (1.0 if po == 1 else 0.0)), po


def load_judge(path, ids):
    p = Path(path)
    if not p.exists():
        sys.exit(f"missing {p}")
    j = json.loads(p.read_text())
    lab = j.get("labels", {})
    bad = [i for i in ids if (lab.get(i) or {}).get("label") not in JUDGE_LABELS]
    if bad:
        sys.exit(f"{p}: {len(bad)} pair(s) without a valid label (fail-closed): {', '.join(bad[:12])}")
    return j.get("model", p.stem), {i: lab[i]["label"] for i in ids}


def cmd_agree(args):
    key_path = KEY_DIR / f"sheet_{args.name}_key.json"
    labels_path = Path(args.labels or REVIEW_DIR / f"labels_{args.name}.json")
    for p in (key_path, labels_path):
        if not p.exists():
            sys.exit(f"missing {p}")
    key = json.loads(key_path.read_text())
    kl = json.loads(labels_path.read_text())
    if kl.get("sheet") != args.name:
        sys.exit("labels file belongs to another sheet")
    ids = sorted(key["pairs"])
    mine = {i: (kl["labels"].get(i) or {}).get("label") for i in ids}
    missing = [i for i in ids if not mine[i]]
    if missing:
        sys.exit(f"Kunaal's labels incomplete: {len(missing)} unlabelled ({', '.join(missing[:12])}). Finish first.")
    cant = [i for i in ids if mine[i] == "cant_tell"]
    print(f"sheet {args.name} · {key['meta']['codebook']} · seed {key['meta']['seed']} · {len(ids)} pairs · "
          f"can't tell {len(cant)} (limit {MAX_CANT_TELL})")
    if len(cant) > MAX_CANT_TELL:
        print(f"TEST VOID: {len(cant)} can't-tell pairs > {MAX_CANT_TELL}. The inputs are too thin to judge; "
              f"fix the cards/input first, then a NEW sheet. No kappa computed.")
        return
    used = [i for i in ids if i not in cant]
    rngseed = 1

    def report(name, path):
        model, theirs = load_judge(path, ids)
        bp = [(int(mine[i] in LINKED), int(theirs[i] in LINKED)) for i in used]
        k, po = kappa(bp)
        rng = random.Random(rngseed)
        boots = sorted(kappa([bp[rng.randrange(len(bp))] for _ in bp])[0] for _ in range(2000))
        lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]
        t = {(a, b): sum(1 for x in bp if x == (a, b)) for a in (1, 0) for b in (1, 0)}
        print(f"\n{name}: {model}  ·  {len(used)} pairs judged")
        print(f"  kappa (linked vs not) {k:.2f}  ·  95% range {lo:.2f} … {hi:.2f}  ·  raw agreement {po:.0%}")
        print(f"  2×2 (Kunaal × judge): both linked {t[(1, 1)]} · only Kunaal {t[(1, 0)]} · "
              f"only judge {t[(0, 1)]} · both not {t[(0, 0)]}")
        order = list(JUDGE_LABELS)
        print("  4×4 (rows Kunaal, cols judge): " + "  ".join(
            f"{r[:4]}[" + " ".join(str(sum(1 for i in used if mine[i] == r and theirs[i] == c)) for c in order) + "]"
            for r in order))
        for role in ("made", "band"):
            sub = [i for i in used if key["pairs"][i]["role"] == role]
            if sub:
                agree_n = sum(1 for i in sub if (mine[i] in LINKED) == (theirs[i] in LINKED))
                print(f"  side note · {role}: agreement {agree_n}/{len(sub)}")
        dis = [i for i in used if (mine[i] in LINKED) != (theirs[i] in LINKED)]
        print(f"  disagreements on the gate ({len(dis)}): {', '.join(dis) if dis else 'none'}")
        return k

    kg = report("GATING JUDGE", args.judge)
    passed = kg >= KAPPA_LINE
    print(f"\nGATE: kappa {kg:.2f} vs line {KAPPA_LINE:.2f} → {'PASS' if passed else 'FAIL'}")
    kd = report("DIAGNOSTIC", args.diag) if args.diag else None
    print("\nOUTCOME (pre-registered 29 Oct):")
    if passed:
        print("  PASS → the judge goes to shadow mode (stored, not linking), then live links.")
    elif kd is None:
        print("  Gating judge FAILED → run the Opus diagnostic and re-run agree with --diag to pick the branch.")
    elif kd >= KAPPA_LINE:
        print("  Haiku FAIL, Opus PASS → the rulebook is fine, the model is too weak; next step decided then.")
    else:
        print("  BOTH FAIL → one codebook revision from the disagreements, then a NEW 100-pair set "
              "(same pairs would be in-sample). A second fail → S6 trigger: revisit the cross-day design.")


# =================================================================================================
# v0.1 · make / score (t01) — unchanged except that make reads T01_TVERSION and the shared card helper
# =================================================================================================
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


def cmd_make(args):
    news, intel = open_ro(NEWS_DB), open_ro(INTEL_DB)
    key_path = KEY_DIR / f"sheet_{args.name}_key.json"
    sheet_path = REVIEW_DIR / f"sheet_{args.name}.html"
    if key_path.exists():
        sys.exit(f"{key_path} exists: refusing to re-draw (blindness). Use a new --name for a new sheet.")

    made = intel.execute(
        "SELECT date, group_id, earlier_date, earlier_group_id, gap_days, avg_score, best_score, centroid_score "
        "FROM links WHERE gversion=? AND tversion=? AND role='parent' ORDER BY date, group_id",
        (GVERSION, T01_TVERSION)).fetchall()
    rejected = intel.execute(
        "SELECT l.date, l.group_id, l.earlier_date, l.earlier_group_id, l.gap_days, l.avg_score, l.best_score, "
        "l.centroid_score FROM links l WHERE l.gversion=? AND l.tversion=? AND l.role='below' AND l.rank=1 "
        "AND l.avg_score >= ? AND l.avg_score < ? AND NOT EXISTS (SELECT 1 FROM links p WHERE p.gversion=l.gversion "
        "AND p.tversion=l.tversion AND p.group_id=l.group_id AND p.role='parent') ORDER BY l.date, l.group_id",
        (GVERSION, T01_TVERSION, BAND_LOW, LINK_LINE)).fetchall()
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
    meta = {"sheet": args.name, "seed": args.seed, "n_each": args.n, "gversion": GVERSION, "tversion": T01_TVERSION,
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
    d = sub.add_parser("draw", help="v0.2: draw the judge-test sheet (+ key + judge inputs)")
    d.add_argument("--name", required=True, help="new sheet name, e.g. x01")
    d.add_argument("--seed", type=int, default=DRAW_SEED)
    d.add_argument("--n", type=int, default=100)
    d.add_argument("--per-date", type=int, default=None, help="cap per later date (only if Kunaal chooses one)")
    d.add_argument("--dry", action="store_true", help="counts only, writes nothing")
    g = sub.add_parser("agree", help="v0.2: kappa of Kunaal vs judge, pre-registered outcome")
    g.add_argument("--name", required=True)
    g.add_argument("--labels", help="path to labels_<name>.json (default review/threads/)")
    g.add_argument("--judge", required=True, help="gating judge labels (Haiku), JSON {model, labels:{id:{label}}}")
    g.add_argument("--diag", help="diagnostic judge labels (Opus), same format")
    m = sub.add_parser("make", help="v0.1 (t01): N made + N rejected, tversion t0.1")
    m.add_argument("--name", default="t01")
    m.add_argument("--seed", type=int, default=2810)
    m.add_argument("--n", type=int, default=20, help="pairs per side (made / rejected)")
    s = sub.add_parser("score", help="v0.1 (t01): the t01 pass lines")
    s.add_argument("--name", default="t01")
    s.add_argument("--labels", help="path to labels_<name>.json (default review/threads/)")
    args = ap.parse_args()
    {"draw": cmd_draw, "agree": cmd_agree, "make": cmd_make, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
