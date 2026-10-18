"""Read-only live smoke test against the real Layrcake API.

It calls get_me (free) and a find_leads dry run, which looks nothing up and
charges nothing. It never launches, sends, buys or approves anything.

    export LAYRCAKE_API_KEY=lc_...
    python examples/smoke.py
"""

from __future__ import annotations

import sys

from layrcake import Layrcake, LayrcakeError


def main() -> int:
    try:
        with Layrcake() as client:
            me = client.get_me()
            print(f"Key {me.result['key']['display_prefix']} in workspace {me.result['workspace']['name']}")
            balance = me.result.get("credits")
            if balance is not None:
                print(f"Credits available: {balance['available']}")

            plan = client.find_leads(
                audience="Heads of sales at US software companies with 50 to 500 staff",
                titles=["Head of Sales"],
                locations=["United States"],
                industries=["Software Development"],
                company_headcount={"min": 50, "max": 500},
                limit=5,
                dry_run=True,
            )
            would = plan.result["plan"]["would"]
            print(
                f"find_leads dry run: at most {would['maximum_credits']} credits, {would['candidate_sources']} sources"
            )
            print(f"Charged for the dry run: {plan.credits_charged} (request {plan.request_id})")
            if plan.credits_charged:
                print("A dry run should never charge.", file=sys.stderr)
                return 1
    except LayrcakeError as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
