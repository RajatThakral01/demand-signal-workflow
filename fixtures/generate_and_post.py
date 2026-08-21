#!/usr/bin/env python3
"""
Fixture seeder — posts synthetic DAXVORA events against the running API.

Usage:
  python fixtures/generate_and_post.py --base-url http://localhost:8000
  python fixtures/generate_and_post.py --base-url http://localhost:8000 --dry-run

Each of the three sources is SIMULATED (PRD §2, LIVE/SIMULATED labels). No
real Reddit / ESP / form provider is called — this simply POSTs the JSON
fixtures at `fixtures/*_events.json` to the app's own POST /api/v1/events.

The script is intentionally small and synchronous (requests, not httpx) so it
can run without the app's async dependencies; it is the evaluator's "one seed
command" referenced in PRD §1 Success Criterion 4 (docker compose up + one seed
command <5 min).

Exit code 0 on full success, 1 if any POST fails.
"""

import argparse
import json
import pathlib
import sys
import time

try:
    import requests
except ImportError:
    print("requests not installed — pip install requests or run via: python -m pip install requests", file=sys.stderr)
    sys.exit(2)

FIXTURE_FILES = [
    "web_form_events.json",
    "social_mention_events.json",
    "email_engagement_events.json",
]

def main() -> int:
    parser = argparse.ArgumentParser(description="Post fixture events to the demand-signal API")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL (default http://localhost:8000)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be posted, do not POST")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    fixtures_dir = pathlib.Path(__file__).resolve().parent

    total = 0
    failed = 0
    start = time.monotonic()

    for fname in FIXTURE_FILES:
        fpath = fixtures_dir / fname
        if not fpath.exists():
            print(f"[skip] {fname} not found", file=sys.stderr)
            continue
        events = json.loads(fpath.read_text(encoding="utf-8"))
        for idx, payload in enumerate(events, 1):
            total += 1
            if args.dry_run:
                print(f"[dry-run] {fname}#{idx} {payload.get('source')}:{payload.get('external_event_id')}")
                continue
            try:
                resp = requests.post(f"{base}/api/v1/events", json=payload, timeout=10)
            except Exception as exc:
                print(f"[error] {fname}#{idx} {payload.get('source')}:{payload.get('external_event_id')} — {exc}", file=sys.stderr)
                failed += 1
                continue
            # PRD Error States: 200 is success even for is_valid=false; only 400 is malformed
            if resp.status_code not in (200, 400):
                print(f"[fail] {fname}#{idx} {payload.get('source')}:{payload.get('external_event_id')} — HTTP {resp.status_code} {resp.text[:200]}", file=sys.stderr)
                failed += 1
            else:
                body = resp.json() if resp.headers.get("content-type","").startswith("application/json") else {}
                flag = "valid" if body.get("is_valid", True) else f"invalid:{body.get('invalid_reason')}"
                if body.get("duplicate"):
                    flag = "duplicate"
                if body.get("is_edit"):
                    flag = "edit"
                if body.get("status") == "manual_review":
                    flag = "manual_review"
                if body.get("status") == "dead_letter":
                    flag = "dead_letter"
                print(f"[ok] {fname}#{idx} {payload.get('source')}:{payload.get('external_event_id')} → {resp.status_code} {flag} event_id={body.get('event_id','')}")

    elapsed = time.monotonic() - start
    print(f"\nSeeded {total - failed}/{total} events in {elapsed:.2f}s")
    # Quick reconciliation check
    if not args.dry_run:
        try:
            r = requests.get(f"{base}/api/v1/dashboard/reconciliation", timeout=10)
            if r.ok:
                j = r.json()
                print(f"Reconciliation: {j.get('status')} variance={j.get('variance')} overall={j.get('overall_status')}")
                if j.get("variance") != 0:
                    print("WARNING: reconciliation variance != 0", file=sys.stderr)
            r2 = requests.get(f"{base}/api/v1/dashboard/summary", timeout=10)
            if r2.ok:
                print(f"Summary: {r2.json()}")
        except Exception as exc:
            print(f"[warn] could not fetch reconciliation/summary: {exc}", file=sys.stderr)

    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
