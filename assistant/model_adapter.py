"""Render verified evidence only. This boundary grants no tool/state capability."""
from typing import Protocol

from .errors import AssistantError
from .retrieval import Evidence


class ModelAdapter(Protocol):
    def render(self, evidence: Evidence, preferences: dict[str, str], *, draft: bool = False) -> str: ...


class DeterministicAdapter:
    mode = "deterministic"

    def render(self, evidence: Evidence, preferences: dict[str, str], *, draft: bool = False) -> str:
        concise = preferences.get("length") == "concise"
        paragraphs = []
        if draft and not concise and preferences.get("introductions") != "omit":
            paragraphs.append("Thank you for contacting support.")
        if evidence.ticket_id:
            facts = f"Ticket {evidence.ticket_id}"
            if evidence.purchase_date:
                facts += f": first payment {evidence.purchase_date}"
            if evidence.elapsed_days is not None:
                facts += f" ({evidence.elapsed_days} calendar days as of {evidence.as_of})"
            if evidence.exports_used is not None:
                facts += f"; premium exports recorded: {evidence.exports_used}"
            paragraphs.append(facts + ".")
        for claim in evidence.claims:
            paragraphs.append(f"{claim['text']} [{claim['document_id']}]")
        outcome = {
            "insufficient_evidence": "Current evidence is insufficient or conflicting. Please verify the missing records or escalate; a refund cannot be confirmed.",
            "manual_escalation": "The recorded facts do not meet the standard refund conditions. Manual escalation is required; a refund cannot be confirmed.",
            "verification_required": "The recorded timing is within the policy window. Verify all conditions before any refund can be confirmed.",
            "policy_only": "Provide the ticket ID to check these conditions against its verified records.",
        }[evidence.assessment]
        paragraphs.append(outcome)
        if evidence.missing:
            names = {"purchase_date": "first payment date", "premium_exports_used": "premium export usage",
                     "prior_credits": "prior credits", "plan_entitlement": "plan entitlement"}
            paragraphs.append("Please verify: " + ", ".join(names[key] for key in evidence.missing) + ".")
        if not concise:
            paragraphs.append("This assessment does not authorize a refund or change subscription state. Any customer message requires separate review and approval.")
        answer = ("\n".join("- " + p for p in paragraphs) if preferences.get("format") == "bullets" else "\n\n".join(paragraphs))
        if len(answer) > 2000:
            raise AssistantError("rendered_body_too_long", 422)
        return answer

