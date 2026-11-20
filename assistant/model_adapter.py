"""Render verified evidence only. This boundary grants no tool/state capability."""
from typing import Protocol

from .errors import AssistantError
from .retrieval import Evidence

_OPERATOR_OUTCOME = {
    "insufficient_evidence": "Current evidence is insufficient or conflicting. Please verify the missing records or escalate; a refund cannot be confirmed.",
    "manual_escalation": "The recorded facts do not meet the standard refund conditions. Manual escalation is required; a refund cannot be confirmed.",
    "verification_required": "The recorded timing is within the policy window. Verify all conditions before any refund can be confirmed.",
    "policy_only": "Provide the ticket ID to check these conditions against its verified records.",
}
_OPERATOR_MISSING = {"purchase_date": "first payment date", "premium_exports_used": "premium export usage",
                     "prior_credits": "prior credits", "plan_entitlement": "plan entitlement"}
# Customer drafts never promise a refund and never expose internal document IDs or procedures.
_CUSTOMER_OUTCOME = {
    "insufficient_evidence": "We need to check a few more details before we can answer your refund question. We cannot confirm a refund at this stage.",
    "manual_escalation": "Your request does not meet the standard refund conditions, so it needs review by our specialist team. We cannot confirm a refund at this stage.",
    "verification_required": "Your request is within the policy window. Before we can confirm a refund, we need to complete a final check of your account.",
    "policy_only": "We cannot confirm a refund at this stage.",
}
_CUSTOMER_MISSING = {"purchase_date": "your first payment date", "premium_exports_used": "premium export usage on your account",
                     "prior_credits": "any prior credits on your account", "plan_entitlement": "your plan entitlement"}
_CUSTOMER_TOPICS = ("refund", "cancellation")  # billing/escalation claims are internal procedure


class ModelAdapter(Protocol):
    def render(self, evidence: Evidence, preferences: dict[str, str], *, draft: bool = False) -> str: ...


class DeterministicAdapter:
    mode = "deterministic"

    def render(self, evidence: Evidence, preferences: dict[str, str], *, draft: bool = False) -> str:
        concise = preferences.get("length") == "concise"
        paragraphs = (self._customer(evidence, preferences, concise) if draft
                      else self._operator(evidence, concise))
        answer = ("\n".join("- " + p for p in paragraphs) if preferences.get("format") == "bullets" else "\n\n".join(paragraphs))
        if len(answer) > 2000:
            raise AssistantError("rendered_body_too_long", 422)
        return answer

    @staticmethod
    def _operator(evidence: Evidence, concise: bool) -> list[str]:
        """Internal investigation: facts, cited claims and what the operator must verify."""
        paragraphs = []
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
        paragraphs.append(_OPERATOR_OUTCOME[evidence.assessment])
        if evidence.missing:
            paragraphs.append("Please verify: " + ", ".join(_OPERATOR_MISSING[key] for key in evidence.missing) + ".")
        if not concise:
            paragraphs.append("This assessment does not authorize a refund or change subscription state. Any customer message requires separate review and approval.")
        return paragraphs

    @staticmethod
    def _customer(evidence: Evidence, preferences: dict[str, str], concise: bool) -> list[str]:
        """Customer-facing comment built from the same verified evidence; citations stay in the response."""
        paragraphs = []
        if not concise and preferences.get("introductions") != "omit":
            paragraphs.append("Thank you for contacting support.")
        if evidence.ticket_id:
            facts = f"We have reviewed your request on ticket {evidence.ticket_id}"
            if evidence.purchase_date:
                facts += f". Our records show your first payment on {evidence.purchase_date}"
            paragraphs.append(facts + ".")
        paragraphs.extend(claim["text"] for claim in evidence.claims if claim["topic"] in _CUSTOMER_TOPICS)
        paragraphs.append(_CUSTOMER_OUTCOME[evidence.assessment])
        if evidence.missing:
            paragraphs.append("We still need to verify " + ", ".join(_CUSTOMER_MISSING[key] for key in evidence.missing) + ".")
        paragraphs.append("We will follow up with you once the review is complete.")
        if not concise:
            paragraphs.append("Thank you for your patience.")
        return paragraphs
