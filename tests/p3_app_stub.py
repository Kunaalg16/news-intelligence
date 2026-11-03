"""p3_app_stub.py -- the suite's stand-in for the data layer, CHOSEN CASES ONLY.
  v1 3 Nov 2026 (K1 integration, I1): replaces `src/p3_app_double.py`, which left the app.

WHAT IT IS
  A handful of canned answers for the page states the real archive cannot be relied on to
  produce on demand: zero, one or two suggestions, each error reason, a cite that no
  passage resolves. A check that needs one hands this module to the app
  (`p3_app.data = p3_app_stub`), picks a case, and puts the real reader back afterwards.
  The app never imports it; a check in the suite's `ownership()` makes sure.

WHY IT HAS NO ALGORITHMS
  Build 2b's double had its own matching rules, and they produced words of their own
  (`'lic'`, `'with'`) and could not produce a short set at all: a fake that computes is a
  second implementation to keep right. So every function here returns what its case says,
  word for word, and computes nothing. If a check needs a new edge case, add a case.

WHAT IT MUST NEVER DO
  Open a database, import a K3 module, reach the network or write a file (checked).
"""

# The case a check has picked. `use()` sets it; `reset()` puts the default back.
CASE = {"name": "default"}

# The fixture switches the dev route flips (contract §8.7); the real reader has the same
# dict, and the app reads whichever module it was handed.
FIXTURES = {"processing": False, "nothing_new": False, "cap_reached": False,
            "assistant_error": False}

# Contract v2.2's failure reasons and whether each may be retried.
ERROR_RETRYABLE = {"model_busy": True, "no_network": True, "timeout": True,
                   "other": True, "key_rejected": False, "checks_failed": False}


def use(name):
    CASE["name"] = name


def reset():
    CASE["name"] = "default"
    for k in FIXTURES:
        FIXTURES[k] = False
    CALLS.clear()
    HIDDEN.clear()


# ---------------------------------------------------------------- I8 · follow-ups
# K3's final pass (contract v2.4 §11.2) is PLANNED, so the page's follow-up display is
# checked against these turns: one per `topic` value, a re-asked answer, one with no
# `topic` at all, and the scopes the two scope corrections need. Shapes from §11.2.

def _answer(answer_id, question, topic, **extra):
    row = {"answer_id": answer_id, "conversation_id": "c_stub", "parent_answer_id": None,
           "question": question, "scope": {"story_id": None, "text_hashes": [],
                                           "filters": {}},
           "model": "haiku", "model_id": "stub", "verdict": "enough", "found_count": 3,
           "sentences": [{"text": "A stub sentence.", "cites": []}], "background": None,
           "cited": [], "found": [], "closest": [], "passage_set_id": None,
           "cost_usd": 0.0008, "attempts": 1, "drops": 0, "at": "2026-11-04T09:00:00",
           "searched_days": 49, "passage_count": 8, "results": [], "results_total": 0,
           "topic": topic, "search_query": question, "carried_from": None,
           "replaces_answer_id": None}
    row.update(extra)
    if topic is None:
        del row["topic"]
    return row


def _row(story_id, headline, group=None):
    row = {"story_id": story_id, "headline": headline, "dates": ["2026-11-03"],
           "papers": 2, "mark": "found"}
    if group:
        row["group"] = group
    return row


FOLLOWUP_TURNS = [
    _answer("f1", "What did the EPFO change about pension payouts?", "first"),
    _answer("f2", "and what did the unions say", "follow_up", carried_from="f1",
            search_query="EPFO pension payout change unions reaction"),
    _answer("f3", "police firing in Manipur", "new_topic", verdict="thin",
            scope={"story_id": "s_manipur", "text_hashes": ["h_m1"], "filters": {}}),
    _answer("f4", "GST rate cut and the RBI repo decision", "two_searches",
            search_query=["GST rate cut", "RBI repo decision"], results_total=3,
            results=[_row("s_rbi", "RBI holds repo rate", "RBI repo decision"),
                     _row("s_gst", "GST Council cuts rates", "GST rate cut"),
                     _row("s_gst2", "States split on GST compensation", "GST rate cut")]),
    _answer("f5", "Musk and the Tesla pay vote", "one_search"),
    _answer("f6", "and the pension arrears", "follow_up", carried_from="f1",
            replaces_answer_id="f0"),
    _answer("f7", "what about the floods", None),
]

FOLLOWUP_CONVERSATION = {"conversation_id": "c_stub", "started_at": "2026-11-04T09:00:00",
                         "turns": [{"kind": "answer", "answer": a} for a in FOLLOWUP_TURNS]}

# What a re-ask under each mode says it did (§11.2: `mode` overrides the rewriter; no
# mode lets it decide, which for f5 is two searches again). A table, not a rule.
REASK_TOPIC = {"as_typed": "new_topic", "carry": "follow_up", "one_search": "one_search",
               None: "two_searches"}

# What the suite's checks read back: every reask / hide_turn call, in order, and the
# answers hidden so far. Lists in memory; nothing is written anywhere.
CALLS = []
HIDDEN = []


def current_conversation():
    return {**FOLLOWUP_CONVERSATION,
            "turns": [t for t in FOLLOWUP_CONVERSATION["turns"]
                      if t["answer"]["answer_id"] not in HIDDEN]}


def _known(answer_id):
    if answer_id not in [a["answer_id"] for a in FOLLOWUP_TURNS]:
        raise LookupError(f"no such answer: {answer_id!r}")


def reask(answer_id, mode=None, scope=None):
    CALLS.append(("reask", answer_id, mode, scope))
    _known(answer_id)
    if mode not in REASK_TOPIC:
        raise ValueError(f"unknown mode: {mode!r}")
    new = _answer("r_" + answer_id, "re-asked " + answer_id, REASK_TOPIC[mode],
                  replaces_answer_id=answer_id)
    if scope is not None:
        new["scope"] = scope
    return iter([{"type": "results", "search": {"stories": [], "total_stories": 0}},
                 {"type": "sentence", "text": "A stub sentence.", "cites": []},
                 {"type": "done", "answer": new}])


def hide_turn(answer_id):
    CALLS.append(("hide_turn", answer_id))
    _known(answer_id)
    HIDDEN.append(answer_id)
