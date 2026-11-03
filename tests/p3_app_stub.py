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
