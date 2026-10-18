"""Find leads, enrich, verify, draft a campaign, then ask a person to approve the launch.

This spends credits on find_leads, enrich and verify_email. The launch call
sends nothing: it returns a preview, and emails go out only after a signed-in
person approves in the Layrcake console.
"""

from __future__ import annotations

from layrcake import Layrcake, approval_required

client = Layrcake()  # reads LAYRCAKE_API_KEY

found = client.find_leads(
    audience="Heads of sales at US software companies with 50 to 500 staff",
    titles=["Head of Sales"],
    locations=["United States"],
    industries=["Software Development"],
    company_headcount={"min": 50, "max": 500},
    limit=5,
)
lead = found.result["leads"][0]

enriched = client.enrich(
    person={"full_name": lead["full_name"] or "", "company_domain": lead["company_domain"] or ""},
    want=["work_email"],
)
contact = enriched.result["contact"]
email = (contact["work_email"] if contact else None) or lead["work_email"]
if not email:
    raise SystemExit("No work email found for this lead.")

check = client.verify_email(email=email)
if check.result["status"] != "valid":
    raise SystemExit(f"{email} is {check.result['status']}")

draft = client.create_campaign(
    name="Q4 sales leaders",
    leads=[{"email": email}],
    steps=[{"subject": "Your outbound this quarter", "body": "Hi {{first_name}}, ..."}],
)

preview = client.launch_campaign(campaign_id=draft.result["campaign_id"])
approval = approval_required(preview)
if approval is not None:
    print("Ask a person to approve:", approval.approval_url or "check the approval email")
