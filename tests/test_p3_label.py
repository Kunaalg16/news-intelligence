"""
test_p3_label.py -- TEST (free, no API key, no internet). Checks src/p3_label.py (`label-1`).

WHAT IT DOES
    Two parts.
    1. RULES (hand-made records, no database): one record per rule in the K3 brief §B1-1,
       and the awkward neighbours of each rule (an empty extra-event list stored three
       different ways, a cue phrase in a news text, a text printed in two seats, a story
       whose only piece is paid). Every rule's expected label is written out by hand.
    2. GOLDEN COUNTS (real data, read-only): the label of every tagged text in
       data/news.db, counted and compared with measurement M1 (`_work/k3_m1_report.md`,
       RAN ON MAC 1 Nov). This is a regression test: it is not asking whether the rules
       are good, it is asking whether they still do what they did when M1 measured them.

WHY IT EXISTS
    The labels decide what the digest counts as coverage, what it hides and what the
    search window may cite. A silent change there is invisible in the output but changes
    every number in the digest, so the counts are pinned to M1 and re-checked on every
    change to p3_label.py.

PASS LINES (fixed before the run, K3 brief §3)
    - Every hand-made rule check passes.
    - text_check: ok 5,052 · fragment 1,203 · two_stories 299 · garbled 101  (M1)
    - layer 'paid' = 104 = the number of kind 'sponsored' texts; no text labelled 'ad'.
    - kind 'other' = 109, and none of them counts as coverage.
    - main event 'speech or statement' = 1,044; of those, no extra event = 642 (M1).
    - The speech swap splits those 1,044 into three outcomes that sum to 1,044, and the
      "kept as a speech" outcome = 528, the number Q1b was decided on.
    - Every notice is out of the digest and still searchable.
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_label.py. Takes about two seconds.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_label.py              rules + golden counts
    .venv/bin/python tests/test_p3_label.py --mock       rules only (no database)
"""

import collections
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import p3_label as L

NEWS_DB = ROOT / "data" / "news.db"

CHECKS = []          # (name, got, want)


def check(name, got, want):
    CHECKS.append((name, got, want))


def rec(**kw):
    """A stored tag record joined to its text, with the fields the labeller reads."""
    base = dict(text_hash="h", kind="news", topic="Politics", summary="A summary.",
                event="policy or regulation", events_more="[]", text_check="ok",
                headline="A headline", deck="", body="Some words.")
    base.update(kw)
    return base


# ------------------------------------------------------------------ part 1: the rules

def rules():
    # --- layer, coverage, digest, caveats -------------------------------------------
    paid = L.label_text(rec(kind="sponsored", topic="Health"))
    check("sponsored -> layer paid", paid["layer"], "paid")
    check("sponsored -> paid flag", paid["paid"], True)
    check("sponsored -> not coverage", paid["counts_as_coverage"], False)
    check("sponsored -> not a story kind", paid["is_story_kind"], False)
    check("sponsored -> caveat", paid["caveats"], ["Paid content"])
    check("sponsored -> in the digest, caveated", paid["in_digest"], True)

    notice = L.label_text(rec(kind="notice", topic=None))
    check("notice -> flag", notice["notice"], True)
    check("notice -> out of the digest", notice["in_digest"], False)
    check("notice -> searchable", notice["searchable"], True)
    check("notice -> not coverage", notice["counts_as_coverage"], False)
    check("notice -> layer editorial", notice["layer"], "editorial")
    check("notice -> no caveat", notice["caveats"], [])

    for kind in ("news", "analysis", "opinion", "interview", "brief", "data"):
        check(f"kind {kind} -> coverage", L.label_text(rec(kind=kind))["counts_as_coverage"], True)
    other = L.label_text(rec(kind="other", topic=None))
    check("kind other -> not coverage (Q1c)", other["counts_as_coverage"], False)
    check("kind other -> shown", other["in_digest"], True)
    check("kind other -> searchable", other["searchable"], True)
    check("no label is 'ad' in v1 (Q1f)", L.PAGE_NOTE_RULE, False)
    check("version string", L.label_text(rec())["label_version"], "label-1")

    # --- the speech swap (Q1b) -------------------------------------------------------
    S = "speech or statement"
    swap = lambda **kw: L.label_text(rec(event=S, **kw))
    check("speech + opinion -> feature", swap(kind="opinion")["event_effective"], "feature")
    check("speech + analysis -> feature", swap(kind="analysis")["event_effective"], "feature")
    check("speech + opinion -> swap reason", swap(kind="opinion")["event_swap"], "feature")
    check("speech + opinion + extra -> still feature",
          swap(kind="opinion", events_more='["protest"]')["event_effective"], "feature")
    check("speech + news + extra -> the extra",
          swap(events_more='["policy or regulation"]')["event_effective"], "policy or regulation")
    check("speech + news + extra -> swap reason",
          swap(events_more='["policy or regulation"]')["event_swap"], "extra")
    check("speech + news + two extras -> the first",
          swap(events_more='["protest", "crime"]')["event_effective"], "protest")
    for name, stored in (("[]", "[]"), ("empty string", ""), ("NULL", None), ("broken json", "[oops")):
        check(f"speech + news + no extra ({name}) -> kept",
              swap(events_more=stored)["event_effective"], S)
        check(f"speech + news + no extra ({name}) -> no swap reason",
              swap(events_more=stored)["event_swap"], "")
    check("speech + news + extra as a list -> the extra",
          swap(events_more=["crime"])["event_effective"], "crime")
    check("speech + news + extra as a bare string -> the extra",
          swap(events_more="crime")["event_effective"], "crime")
    check("speech + sponsored + extra -> the extra (kind is not editorial)",
          swap(kind="sponsored", events_more='["launch"]')["event_effective"], "launch")
    other_event = L.label_text(rec(event="crime", events_more='["protest"]'))
    check("a non-speech event is untouched", other_event["event_effective"], "crime")
    check("a non-speech event has no swap reason", other_event["event_swap"], "")
    check("the stored event is kept beside it", swap(kind="opinion")["event"], S)

    # --- text_check (Q1g) ------------------------------------------------------------
    for value, fields in (("ok", (False, False, False, False)),
                          ("fragment", (True, False, False, True)),
                          ("two_stories", (False, True, False, False)),
                          ("garbled", (False, False, True, True))):
        lab = L.label_text(rec(text_check=value))
        got = (lab["fragment"], lab["two_stories"], lab["garbled"], lab["fragment_like"])
        check(f"text_check {value} -> (fragment, two_stories, garbled, fragment_like)", got, fields)

    # --- the report-only cue rule (Q1e) ----------------------------------------------
    cue_body = L.label_text(rec(body="This is a promotional feature for a bank."))
    check("cue in the body is reported", cue_body["cue_rule_hit"], "promotional feature")
    check("a cue does NOT make a news text paid", cue_body["layer"], "editorial")
    check("a cue does NOT remove it from coverage", cue_body["counts_as_coverage"], True)
    check("cue in the deck is reported",
          L.label_text(rec(deck="From the BRAND STUDIO"))["cue_rule_hit"], "brand studio")
    check("cue in the headline is reported",
          L.label_text(rec(headline="Our new Partner Content"))["cue_rule_hit"], "partner content")
    check("the widened treat-as cue is reported",
          L.label_text(rec(body="Treat these as advertisements, readers."))["cue_rule_hit"],
          "treat these as advertisements")
    check("treat-as with no advertisement nearby is not a cue",
          L.label_text(rec(body="Treat these as a warning sign for the market."))["cue_rule_hit"], "")
    check("the dropped phrase is no longer a cue (chat 13)",
          L.label_text(rec(body="Held in association with a bank."))["cue_rule_hit"], "")
    check("no cue -> empty string", L.label_text(rec())["cue_rule_hit"], "")
    check("no text to look in -> None (not 'no cue')",
          L.label_text(dict(kind="news", text_check="ok"))["cue_rule_hit"], None)

    # --- seats (Q1a) ------------------------------------------------------------------
    day = {"date": "2026-10-24", "publisher": "Mint", "seats": [
        dict(rec(text_hash="t1"), art_id="a1", page=1, number=2, byline="Our reporter"),
        dict(rec(text_hash="t1"), art_id="a9", page=12, number=1, byline="Our reporter"),
        {"art_id": "a2", "page": 7, "number": 1, "byline": "", "text_hash": "t2",
         "record": rec(text_hash="t2", kind="sponsored")},
    ]}
    seats = L.label_seats(day)
    check("one label per seat", len(seats), 3)
    check("a text printed twice gives two labels with one hash",
          [s["text_hash"] for s in seats[:2]], ["t1", "t1"])
    check("seat keeps its own page", [s["page"] for s in seats], [1, 12, 7])
    check("seat keeps its own art_id", [s["art_id"] for s in seats], ["a1", "a9", "a2"])
    check("seat carries the day", seats[0]["date"], "2026-10-24")
    check("seat carries the paper", seats[0]["publisher"], "Mint")
    check("seat carries the byline", seats[0]["byline"], "Our reporter")
    check("a seat inherits its text's label (v1)", seats[0]["counts_as_coverage"], True)
    check("a nested record is read", seats[2]["layer"], "paid")
    check("page-note slot present and off", seats[0]["page_note_ad"], None)
    check("no seats -> no labels", L.label_seats({"date": "d", "publisher": "p"}), [])

    # --- story flags (Q1d, 7d) --------------------------------------------------------
    seat = lambda pub, **kw: dict(L.label_text(rec(**kw)), publisher=pub)
    two_papers = [seat("Mint"), seat("The Hindu")]
    flags = L.story_flags(two_papers)
    check("two papers -> not single source", flags["single_source"], False)
    check("two papers -> no paid content", flags["paid_content"], False)
    check("two papers -> not paid only", flags["paid_only"], False)
    check("two papers, full text -> not partial", flags["partial_text"], False)
    check("one paper, two seats -> single source",
          L.story_flags([seat("Mint"), seat("Mint")])["single_source"], True)
    check("one paper plus a paid piece from another -> still single source",
          L.story_flags([seat("Mint"), seat("BL", kind="sponsored")])["single_source"], True)

    mixed = L.story_flags([seat("Mint"), seat("BL", kind="sponsored")])
    check("a paid piece in the group -> paid_content", mixed["paid_content"], True)
    check("but editorial coverage exists -> not paid_only", mixed["paid_only"], False)
    paid_only = L.story_flags([seat("BL", kind="sponsored"), seat("Mint", kind="notice")])
    check("only paid and a notice -> paid_only", paid_only["paid_only"], True)
    check("only paid -> single source unknown", paid_only["single_source"], None)
    check("only paid -> not partial text", paid_only["partial_text"], False)

    check("every covering piece is a fragment -> partial text",
          L.story_flags([seat("Mint", text_check="fragment"),
                         seat("BL", text_check="garbled")])["partial_text"], True)
    check("one full piece among fragments -> not partial",
          L.story_flags([seat("Mint", text_check="fragment"), seat("BL")])["partial_text"], False)
    check("a notice beside one fragment does not save it (coverage only)",
          L.story_flags([seat("Mint", text_check="fragment"),
                         seat("BL", kind="notice")])["partial_text"], True)
    check("text labels without a paper -> single source unknown",
          L.story_flags([L.label_text(rec()), L.label_text(rec())])["single_source"], None)
    check("one label with no paper -> unknown, not a wrong False",
          L.story_flags([seat("Mint"), L.label_text(rec())])["single_source"], None)
    empty = L.story_flags([])
    check("no labels -> no flags raised",
          (empty["paid_content"], empty["paid_only"], empty["partial_text"]), (False, False, False))
    check("no labels -> single source unknown", empty["single_source"], None)


# ------------------------------------------------- part 2: golden counts on real data

def golden():
    con = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT t.text_hash, t.kind, t.topic, t.summary, t.event, t.events_more, t.text_check, "
        "x.headline, x.body FROM tags t JOIN texts x ON x.text_hash = t.text_hash "
        "WHERE t.model = ? AND t.prompt_version = ?", (L.TAG_MODEL, L.TAG_PROMPT)).fetchall()
    cols = ("text_hash", "kind", "topic", "summary", "event", "events_more", "text_check",
            "headline", "body")
    labels = [L.label_text(dict(zip(cols, r))) for r in rows]
    con.close()

    n = collections.Counter
    checks = n(l["text_check"] for l in labels)
    check("M1 text_check ok", checks["ok"], 5052)
    check("M1 text_check fragment", checks["fragment"], 1203)
    check("M1 text_check two_stories", checks["two_stories"], 299)
    check("M1 text_check garbled", checks["garbled"], 101)
    check("M1 texts tagged", len(labels), 6655)

    kinds = n(l["kind"] for l in labels)
    check("M1 sponsored texts", kinds["sponsored"], 104)
    check("layer paid = sponsored texts", sum(l["layer"] == "paid" for l in labels), 104)
    check("no text labelled 'ad' in v1", sum(l["layer"] == "ad" for l in labels), 0)
    check("M1 kind other", kinds["other"], 109)
    check("no 'other' counts as coverage",
          sum(l["counts_as_coverage"] for l in labels if l["kind"] == "other"), 0)
    check("no paid piece counts as coverage", sum(l["counts_as_coverage"] and l["paid"] for l in labels), 0)
    check("no notice counts as coverage",
          sum(l["counts_as_coverage"] for l in labels if l["notice"]), 0)
    check("every notice is out of the digest",
          sum(l["in_digest"] for l in labels if l["notice"]), 0)
    check("every notice stays searchable",
          sum(l["searchable"] for l in labels if l["notice"]), kinds["notice"])

    speech = [l for l in labels if l["event"] == "speech or statement"]
    check("M1 main event speech", len(speech), 1044)
    check("M1 speech with no extra event",
          sum(1 for r in rows if r[4] == "speech or statement" and (r[5] in (None, "", "[]"))), 642)
    swaps = n(l["event_swap"] for l in speech)
    check("the swap covers every speech text", sum(swaps.values()), 1044)
    check("speech kept as a speech = the Q1b number", swaps[""], 528)

    return dict(labels=len(labels), kinds=kinds, swaps=swaps,
                coverage=sum(l["counts_as_coverage"] for l in labels),
                fragment_like=sum(l["fragment_like"] for l in labels),
                cue_hits=sum(1 for l in labels if l["cue_rule_hit"]),
                cue_not_paid=sum(1 for l in labels if l["cue_rule_hit"] and not l["paid"]))


def main():
    mock_only = "--mock" in sys.argv
    rules()
    numbers = None
    if mock_only:
        print("golden counts: SKIPPED (--mock)")
    elif not NEWS_DB.exists():
        print(f"golden counts: SKIPPED ({NEWS_DB} not found)")
    else:
        numbers = golden()

    bad = [(name, got, want) for name, got, want in CHECKS if got != want]
    for name, got, want in bad:
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"({'rules only' if numbers is None else 'rules + golden counts'})")

    if numbers:
        k = numbers["kinds"]
        print(f"labels: {numbers['labels']} texts · coverage {numbers['coverage']} · "
              f"fragment-like {numbers['fragment_like']} · paid {k['sponsored']} · "
              f"notice {k['notice']} · other {k['other']}")
        s = numbers["swaps"]
        print(f"speech swap: kept {s['']} · -> feature {s['feature']} · -> extra {s['extra']}")
        print(f"cue rule (report only): {numbers['cue_hits']} texts matched, "
              f"{numbers['cue_not_paid']} of them not kind sponsored")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
