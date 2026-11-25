"""Score the agent on the 16 sample emails against their ground_truth labels.

Usage:  .venv/bin/python scripts/eval.py            # all emails
        .venv/bin/python scripts/eval.py EM002 EM013  # just these
        .venv/bin/python scripts/eval.py --seed     # all emails, and save the runs as
                                                    # data/seed_runs.json (pre-loaded demo runs)

Metrics:
- intent: draft intent == ground-truth intent
- part top-3: for emails with expected parts, at least one of the agent's first 3
  recommended SKUs has an expected part number. For a part question with NO
  expected part (unidentifiable photo), correct = agent recommends nothing.
- order ID: draft order_id == ground-truth order_id (emails that have one)
- urgency: draft urgency == ground-truth urgency
Writes full traces to results/eval_<time>.json.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import MODEL, run_agent  # noqa: E402
from app.tools import BY_SKU  # noqa: E402

# Sonnet 5.5 list prices, $ per million tokens (cache reads are cheaper).
PRICE_IN, PRICE_OUT, PRICE_CACHE_READ = 2.00, 10.00, 0.10


def run_one(email: dict) -> dict:
    events = list(run_agent(email))
    final = next((e["draft"] for e in events if e["type"] == "final"), None)
    usage = next((e for e in events if e["type"] == "usage"), {})
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    return {"id": email["id"], "events": events, "draft": final, "usage": usage, "error": error}


def score(email: dict, run: dict) -> dict:
    gt, d = email["ground_truth"], run["draft"] or {}
    s = {"intent": d.get("intent") == gt["intent"], "urgency": d.get("urgency") == gt["urgency"]}
    recs = [BY_SKU.get(sku, {}).get("part_number") for sku in (d.get("recommended_skus") or [])[:3]]
    if gt["expected_part_numbers"]:
        s["part_top3"] = any(p in gt["expected_part_numbers"] for p in recs)
    elif gt["intent"] == "part_identification":
        s["part_top3"] = len(recs) == 0  # should admit it can't identify the part
    if gt["order_id"]:
        s["order_id"] = (d.get("order_id") or "").upper() == gt["order_id"]
    s["_recs"] = recs
    return s


def main():
    args = [a for a in sys.argv[1:] if a != "--seed"]
    make_seed = "--seed" in sys.argv[1:]
    emails = json.loads((ROOT / "data" / "emails.json").read_text())
    if args:
        emails = [e for e in emails if e["id"] in args]
    print(f"Running {len(emails)} emails on {MODEL} ...")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(run_one, emails))
    wall = time.time() - t0

    totals: dict[str, list[bool]] = {}
    rows = []
    for email, run in zip(emails, runs):
        s = score(email, run)
        for k, v in s.items():
            if not k.startswith("_"):
                totals.setdefault(k, []).append(v)
        d = run["draft"] or {}
        mark = lambda k: "" if k not in s else ("ok" if s[k] else "XX")  # noqa: E731
        rows.append(f"{email['id']}  {mark('intent'):2} {d.get('intent', 'ERROR'):20} "
                    f"part:{mark('part_top3'):2} order:{mark('order_id'):2} urg:{mark('urgency'):2} "
                    f"recs={s['_recs']} want={email['ground_truth']['expected_part_numbers']}"
                    + (f"  ERROR: {run['error']}" if run["error"] else ""))
    print("\n".join(rows))

    u_in = sum(r["usage"].get("input_tokens", 0) for r in runs)
    u_out = sum(r["usage"].get("output_tokens", 0) for r in runs)
    u_cache = sum(r["usage"].get("cache_read_input_tokens", 0) for r in runs)
    cost = (u_in * PRICE_IN + u_out * PRICE_OUT + u_cache * PRICE_CACHE_READ) / 1e6
    secs = [r["usage"].get("seconds", 0) for r in runs if r["usage"]]

    print("\nSCORES")
    for k, v in totals.items():
        print(f"  {k:10} {sum(v)}/{len(v)}  ({100 * sum(v) / len(v):.0f}%)")
    print(f"\nCost ~${cost:.3f} total, ~${cost / len(runs):.4f} per email; "
          f"avg {sum(secs) / max(len(secs), 1):.1f}s per email; wall {wall:.0f}s")

    out = ROOT / "results" / f"eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "model": MODEL,
        "scores": {k: f"{sum(v)}/{len(v)}" for k, v in totals.items()},
        "cost_usd": round(cost, 4),
        "runs": runs,
    }, indent=2, default=str))
    print(f"Traces saved to {out.relative_to(ROOT)}")

    if make_seed:
        # Only runs that produced a draft become seeds; the web app loads them into SQLite on startup.
        seed = {r["id"]: r["events"] for r in runs if r["draft"]}
        seed_path = ROOT / "data" / "seed_runs.json"
        seed_path.write_text(json.dumps(seed, default=str))
        print(f"Saved {len(seed)} runs as demo seeds to {seed_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
