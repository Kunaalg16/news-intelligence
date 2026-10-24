"""
p2_dev_voyage_limits.py -- measure this account's Voyage rate limits (dev tool, 24 Oct 2026)

WHAT IT DOES
  1. Tokens: binary search for the largest single request (in tokens) the API accepts.
     Each probe waits WAIT seconds first, so the per-minute window is empty and a probe is never
     refused because of an earlier one. A 429 lowers the upper bound, a 200 raises the lower bound.
  2. Requests: after a pause, sends tiny 1-word requests back to back until the first 429
     (stops at 30) -> how many requests fit in one minute.
  Prints only sizes, status codes and token counts. The key is read by p2_embed.read_key (never printed).

WHY
  Atlas does not publish the limits of an account without a payment method. p2_embed.py --tpm/--rpm
  then paces a full pass just under the measured limits.

HOW TO RUN (from ~/news-intel)
  .venv-embed/bin/python src/p2_dev_voyage_limits.py            about 8-10 minutes; uses a few 10,000 free tokens
  Options: --lo 500 --hi 40000 (token range) --wait 65 --precision 0.1 (stop when hi/lo < 1.1)
"""
import argparse, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from p2_embed import VOYAGE_URL, read_key

FILLER = "newspaper "          # about 1 token per word for Voyage; the API reports the true count


def call(key, texts):
    body = json.dumps({"input": texts, "model": "voyage-4", "input_type": "document"}).encode("utf-8")
    req = urllib.request.Request(VOYAGE_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, int(json.load(r).get("usage", {}).get("total_tokens", 0))
    except urllib.error.HTTPError as e:
        return e.code, 0


def probe_tokens(key, words, wait):
    """One request of about `words` tokens, split into texts of <= 2,000 words (per-text cap)."""
    time.sleep(wait)
    chunks = [FILLER * min(2000, words - i) for i in range(0, words, 2000)]
    return call(key, chunks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lo", type=int, default=500)
    ap.add_argument("--hi", type=int, default=40000)
    ap.add_argument("--wait", type=float, default=65)
    ap.add_argument("--precision", type=float, default=0.1)
    args = ap.parse_args()
    key = read_key("VOYAGE_API_KEY")
    t0 = time.time()

    print(f"1 tokens per minute: binary search {args.lo}-{args.hi} words, {args.wait:.0f}s pause before each probe")
    code, tok = probe_tokens(key, args.lo, args.wait)
    print(f"  {args.lo:>6} words -> HTTP {code}, {tok} tokens")
    if code != 200:
        sys.exit("  the smallest probe was refused; check the key or the project's own limit in Atlas.")
    lo, lo_tok, hi = args.lo, tok, args.hi
    while hi / lo > 1 + args.precision:
        mid = (lo + hi) // 2
        code, tok = probe_tokens(key, mid, args.wait)
        print(f"  {mid:>6} words -> HTTP {code}" + (f", {tok} tokens" if tok else ""))
        if code == 200:
            lo, lo_tok = mid, tok
        elif code == 429:
            hi = mid
        else:
            sys.exit(f"  unexpected HTTP {code}; stopped.")
    print(f"  => largest accepted: {lo_tok} tokens in one minute (refused at about {hi} words)")

    print(f"2 requests per minute: {args.wait:.0f}s pause, then 1-word requests until the first 429 (max 30)")
    time.sleep(args.wait)
    n, start = 0, time.time()
    while n < 30:
        code, _ = call(key, ["newspaper"])
        if code != 200:
            print(f"  request {n + 1} -> HTTP {code} after {time.time() - start:.1f}s")
            break
        n += 1
    print(f"  => {n} requests accepted in {time.time() - start:.1f}s" + ("" if n < 30 else " (no limit hit; at least 30)"))
    print(f"suggested: --tpm {int(lo_tok * 0.9)} --rpm {max(1, n - 1) if n < 30 else 30}   "
          f"(90% of the measured tokens, one request below the limit) · total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
