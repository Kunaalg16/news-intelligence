"""p3_label.py — Phase 3 · Read-time labelling v1 (`label-1`).
  v0.1 1 Nov 2026 (K3 B1-1): first build, from K3 build brief 1 §B1-1 and the K3 note Q1.

WHAT IT DOES
  Turns one stored tag record (model claude-haiku-5-5, prompt v1.3) plus its text
  into a LABEL: a small dict saying what this piece of printing is, for the digest,
  the search window and the article page. Three functions, no state, no database:
      label_text(record)      one text  -> one label
      label_seats(paper_day)  one paper on one day -> one label per seat (printed slot)
      story_flags(labels)     the labels of a story -> four story-level flags
  Nothing here is stored: labels are computed when something is read.

WHY
  The tags are the model's answer about a text; they are not the reader's view of it.
  A paid feature is tagged like an article, a classified notice is tagged like an
  article, and a main event of "speech or statement" hides what the speech was about.
  Correcting that at read time (schema-on-read) means one rule set behind every
  surface, changeable without a re-tag ($3.18 and a day each time), and every
  correction is traceable to a version string (`LABEL_VERSION`) stored on the visit
  record. Design of record: `_docs/2026-11-01_K3_digest_search.md` Q1 (a)-(g),
  carried from S5b §2 / S6 D7; build spec: `_docs/K3_build_brief_1.md` §B1-1.

WHAT A LABEL SAYS
  layer               'paid' (bought space) or 'editorial'. 'ad' exists in the
                      vocabulary but no v1 label carries it: whole-page ads are
                      dropped at extraction (2.1) and never become texts, and the
                      page-note rule that would mark an editorial-looking seat as
                      bought is OFF in v1 (Q1f, parked until the printed->PDF page
                      offset is known).
  notice              a classified / results / obituary notice: out of the digest,
                      still searchable (Q1).
  counts_as_coverage  does this piece count when we say "five papers covered it"?
                      True for news, analysis, opinion, interview, brief, data.
                      False for paid, notices and kind 'other' (Q1c).
  event_effective     the main event with the "speech or statement" swap applied
                      (Q1b): an opinion or analysis piece about a speech is really a
                      feature; otherwise the extra event says what the speech was
                      about; with neither, "speech or statement" is kept (528 texts,
                      the number Q1b was decided on).
  fragment/two_stories/garbled   the stored text_check, with `garbled` treated like
                      `fragment` everywhere downstream (Q1g) via `fragment_like`.
  caveats             reader-facing warnings for this piece: "Paid content" only, no
                      sponsor names in v1 (Q1, S5b).
  cue_rule_hit        REPORT ONLY. The corrected sponsored-cue phrase rule. It never
                      moves a label: M1 measured its precision at about zero (12
                      texts caught, 3 of them not sponsored at all), so v1 sponsored
                      = model kind only (Q1e). Kept so the build report can count it.

DATA (one owner per file)
  reads   nothing. Pure functions over dicts handed in by the caller.
  writes  nothing.
  The caller reads `news.db` read-only (`file:data/news.db?mode=ro`) and hands over
  rows; `p3_group.py` and the digest are expected to import this module so that the
  excluded-kind set and the paid predicate have a single source of truth (K3 note §2).

HOW TO RUN
  Imported, not run. Its test is a script:
      .venv/bin/python tests/test_p3_label.py            hand-made rules + golden counts
      .venv/bin/python tests/test_p3_label.py --mock      hand-made rules only (no database)

KNOBS
  Every value chosen without strong evidence is in the KNOBS block below with its
  reason and the measurement that would change it (K3 note §5). A knob change bumps
  LABEL_VERSION, because stored visit records name the version that produced them.
"""

import json

# ---------------------------------------------------------------- KNOBS (K3 note §5)

LABEL_VERSION = "label-1"          # rule set behind every label; stored on visit records

# Which tag record this module reads. A new prompt version re-tags every text, so the
# pair below pins what the rules were written against (K3 note §5 "Tag record read").
TAG_MODEL = "claude-haiku-5-5"
TAG_PROMPT = "v1.3"

# Kinds that count as coverage of a story. 'other' is out in v1 (Q1c): a 15-item sample
# found 3 of 8 'other' texts to be real news, so it is shown and searchable but never
# counted; reviewed at the first pass. 'sponsored' and 'notice' are out by definition.
COVERAGE_KINDS = ("news", "analysis", "opinion", "interview", "brief", "data")

# Bought space. v1 = model kind only (Q1e); the phrase rule below is report-only.
PAID_KINDS = ("sponsored",)

# Kinds that are not stories: they take no part in grouping stage 2 and no part in the
# digest. Same set as `p3_group.py` TRACK_KNOBS["exclude_kinds"] (t0.2), on purpose.
NON_STORY_KINDS = ("notice", "sponsored")

# text_check values treated as "the text we have is not the whole article". `garbled`
# joins `fragment` (Q1g): both mean the words on the page did not arrive intact.
FRAGMENT_CHECKS = ("fragment", "garbled")

# The "speech or statement" swap (Q1b). An opinion or analysis piece whose main event is
# a speech is a feature about it; a news piece's extra event says what the speech was
# about; with neither, the speech is kept as the event (M1: 628 texts, first pass item).
SPEECH_EVENT = "speech or statement"
SPEECH_TO_FEATURE_KINDS = ("opinion", "analysis")
SPEECH_FALLBACK_EVENT = "feature"

# Page-note rule OFF in v1 (Q1f): seats on a page that an advertising note names would
# become layer 'ad'. Parked until the printed->PDF offset is stored; M1 found 5 such
# notes (Mint, paid seat 2 PDF pages after the named page).
PAGE_NOTE_RULE = False

# Reader-facing caveat text. No sponsor names in v1 (S5b, overriding 24 Oct).
CAVEAT_PAID = "Paid content"

# ------------------------------------------------- report-only sponsored cue rule (Q1e)
# The corrected phrase list, as measured in M1 (`_work/k3_m1_report.md` §2b): "in
# association with" dropped (chat 13), "treat it/them/these as advertisement(s)" widened,
# four phrases added. M1 result: 12 texts caught, 9 of them already kind 'sponsored',
# 3 not sponsored at all (2 page notes in unrelated stories, 1 article about advertising)
# -> precision about zero, so it decides nothing. Recomputed here only so the build
# report can count it. Copied, not imported: the throwaway M1 script is not a module.
CUE_PHRASES = (
    "promotional feature", "advertorial", "marketing initiative",
    "consumer connect initiative", "impact feature", "brand connect", "partner content",
    "sponsored content", "sponsored feature", "marketing feature",
    "reader engagement initiative", "health awareness initiative", "commercial feature",
    "brand studio",
)
CUE_TREAT = "treat it as", "treat them as", "treat these as"   # ... advertisement(s)


def cue_match(record):
    """REPORT ONLY (Q1e). The matched cue phrase in headline + deck + body, else ''.

    Returns None when the record carries no text to search (a tags-only row), so that
    "no cue" and "could not look" stay apart in the build report.
    """
    parts = [record.get(k) or "" for k in ("headline", "deck", "body")]
    if not any(parts):
        return None
    hay = " ".join(parts).lower()
    for phrase in CUE_PHRASES:
        if phrase in hay:
            return phrase
    for opener in CUE_TREAT:
        at = hay.find(opener)
        if at >= 0 and "advertisement" in hay[at:at + len(opener) + 20]:
            return opener + " advertisements"
    return ""


# ------------------------------------------------------------------------ the functions

def _events_more(value):
    """The stored extra events as a list. '[]', '', None and a bare string all work."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return []
        return [str(v) for v in parsed if v] if isinstance(parsed, list) else []
    return [text]


def event_effective(kind, event, events_more):
    """The main event with the speech swap applied (Q1b). Also returns why it changed.

    Returns (event_effective, swap) where swap is '' (nothing changed), 'feature'
    (opinion/analysis about a speech) or 'extra' (the extra event took over).
    """
    if event != SPEECH_EVENT:
        return event, ""
    if kind in SPEECH_TO_FEATURE_KINDS:
        return SPEECH_FALLBACK_EVENT, "feature"
    extra = _events_more(events_more)
    if extra:
        return extra[0], "extra"
    return event, ""


def label_text(record):
    """One label for one text. `record` = a stored tag row joined to its text.

    Read: kind, event, events_more, text_check, and (optional, for the report-only cue
    rule) headline, deck, body. Anything else in the record is ignored; text_hash,
    topic and summary are passed through for the caller's convenience.
    """
    kind = record.get("kind")
    check = record.get("text_check")
    paid = kind in PAID_KINDS
    notice = kind == "notice"
    fragment = check == "fragment"
    garbled = check == "garbled"
    effective, swap = event_effective(kind, record.get("event"), record.get("events_more"))

    layer = "paid" if paid else "editorial"     # 'ad' unreachable in v1: see PAGE_NOTE_RULE
    caveats = [CAVEAT_PAID] if paid else []

    return {
        "label_version": LABEL_VERSION,
        "text_hash": record.get("text_hash"),
        "kind": kind,
        "layer": layer,
        "paid": paid,
        "notice": notice,
        "in_digest": not notice and layer != "ad",
        "searchable": True,                     # every kind is indexed (Q10d)
        "counts_as_coverage": kind in COVERAGE_KINDS,
        "is_story_kind": kind not in NON_STORY_KINDS,
        "event": record.get("event"),
        "event_effective": effective,
        "event_swap": swap,
        "text_check": check,
        "fragment": fragment,
        "two_stories": check == "two_stories",
        "garbled": garbled,
        "fragment_like": fragment or garbled,   # Q1g: garbled handled as fragment
        "topic": record.get("topic"),
        "summary": record.get("summary"),
        "caveats": caveats,
        "cue_rule_hit": cue_match(record),      # report only, never moves the label
    }


SEAT_FIELDS = ("art_id", "page", "number", "deck", "byline", "text_hash")


def label_seats(paper_day):
    """One label per printed slot for one paper on one day (Q1a).

    `paper_day` = {"date": D, "publisher": P, "seats": [seat, ...]}. A seat is a
    `placements` row; it carries its tag record either under "record" or, when the
    caller joined the tables, in the seat dict itself. In v1 a seat inherits its text's
    label unchanged (the page-note slot exists but is off), and adds where it was
    printed. A text printed twice gives two seat labels with the same text_hash.
    """
    date, publisher = paper_day.get("date"), paper_day.get("publisher")
    out = []
    for seat in paper_day.get("seats") or []:
        record = seat.get("record") or seat
        label = label_text(record)
        label.update(date=date, publisher=publisher,
                     **{f: seat.get(f, label.get(f)) for f in SEAT_FIELDS})
        label["page_note_ad"] = False if PAGE_NOTE_RULE else None   # Q1f: rule off
        out.append(label)
    return out


def story_flags(labels):
    """The four story-level flags (Q1d, 7d) over the labels of one story group.

    Computed over the story's COVERAGE labels — the pieces that make it a story — so a
    paid feature or a notice sitting in the group cannot make the story look partial or
    single-sourced. `single_source` needs seat labels (they carry `publisher`); given
    text labels it cannot be known and comes back None.
    """
    labels = list(labels or [])
    coverage = [l for l in labels if l.get("counts_as_coverage")]
    papers = {l.get("publisher") for l in coverage if l.get("publisher")}

    if not coverage or any(not l.get("publisher") for l in coverage):
        single_source = None        # no coverage at all, or a label without a publisher
    else:
        single_source = len(papers) == 1

    return {
        "paid_content": any(l.get("paid") for l in labels),
        "paid_only": any(l.get("paid") for l in labels) and not coverage,
        "single_source": single_source,
        "partial_text": bool(coverage) and all(l.get("fragment_like") for l in coverage),
    }
