"""Metadata first, explainable lexical ranking, then bounded policy fact parsing."""
import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import ValidationError

from .schemas import Document, Ticket


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def topic(document: Document) -> str | None:
    title = tokens(document.title)
    if {"refund", "policy"} <= title:
        return "refund"
    if {"billing", "verification"} <= title:
        return "billing"
    if "cancellation" in title and ("instructions" in title or "policy" in title):
        return "cancellation"
    if {"escalation", "handbook"} <= title:
        return "escalation"
    return None


def refund_terms(document: Document) -> tuple[int | None, bool, bool]:
    text = re.sub(r"\s+", " ", document.text.lower()).strip()
    # Recognize complete affirmative rules in this tiny corpus. An arbitrary
    # occurrence of "within N days" cannot establish eligibility or its anchor.
    rule = re.fullmatch(
        r"(?:(?:[a-z]+ paid-plan )?refunds are available|[a-z]+ customers may request a subscription refund)"
        r" within (?P<days>\d{1,3}) calendar days of (?:the first payment|the original payment)"
        r"(?P<exports>, if no premium exports have been used)?"
        r"(?P<enterprise>; enterprise plans are excluded)?"
        r"(?:\. (?:refunds outside this window require manual escalation|do not promise a refund before verification|a support representative must verify entitlement))*\.",
        text,
    )
    if rule is None:
        return (None, False, False)
    return (int(rule.group("days")), bool(rule.group("exports")), bool(rule.group("enterprise")))


@dataclass
class Retrieval:
    documents: list[Document] = field(default_factory=list)
    encountered_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    ranking: list[dict] = field(default_factory=list)


def select_documents(raw_documents: list[dict], tenant: str, query: str, today: date) -> Retrieval:
    result = Retrieval()
    query_tokens = tokens(query)
    wanted: list[str] = []
    if query_tokens & {"refund", "refunds", "billing", "payment", "charges", "paid"}:
        wanted.extend(["refund", "billing", "escalation"])
    if query_tokens & {"cancel", "cancellation", "renewal"}:
        wanted.extend(["cancellation", "refund", "billing"])
    wanted = list(dict.fromkeys(wanted))
    candidates: dict[str, list[tuple[Document, int]]] = {}
    for raw in raw_documents:
        try:
            document = Document.model_validate(raw)
        except ValidationError:
            result.issues.append("malformed_document_excluded")
            continue
        if document.tenant_id not in (tenant, "global"):
            continue
        result.encountered_ids.append(document.id)
        if document.status != "active" or document.effective_at > today:
            continue
        kind = topic(document)
        if kind not in wanted:
            continue
        score = 10 + 3 * len(query_tokens & tokens(document.title)) + len(query_tokens & tokens(document.text))
        candidates.setdefault(kind, []).append((document, score))
    for kind in wanted:
        matches = candidates.get(kind, [])
        if not matches:
            continue
        specific = [(d, s) for d, s in matches if d.tenant_id == tenant]
        matches = specific or matches
        # Multiple active contradictory constraints are not resolved by lexical score.
        if kind == "refund" and len({refund_terms(d) for d, _ in matches}) > 1:
            result.issues.append("conflicting_refund_policies")
            continue
        matches.sort(key=lambda pair: (pair[0].effective_at, pair[1], pair[0].id), reverse=True)
        newest = matches[0][0].effective_at
        tied = [d for d, _ in matches if d.effective_at == newest]
        if len({d.text for d in tied}) > 1:
            result.issues.append(f"conflicting_{kind}_policies")
            continue
        selected, score = matches[0]
        result.documents.append(selected)
        result.ranking.append({"id": selected.id, "topic": kind, "lexical_score": score,
                               "authority": "tenant" if specific else "global", "effective_at": str(selected.effective_at)})
    return result


@dataclass
class Evidence:
    ticket_id: str | None
    ticket_version: int | None
    as_of: date
    purchase_date: date | None = None
    exports_used: int | None = None
    window_days: int | None = None
    elapsed_days: int | None = None
    assessment: str = "insufficient_evidence"
    claims: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def citations(self) -> list[str]:
        return list(dict.fromkeys(c["document_id"] for c in self.claims))

    def public(self) -> dict:
        return {"ticket_id": self.ticket_id, "ticket_version": self.ticket_version,
                "as_of": str(self.as_of), "purchase_date": str(self.purchase_date) if self.purchase_date else None,
                "premium_exports_used": self.exports_used, "window_days": self.window_days,
                "elapsed_days": self.elapsed_days, "assessment": self.assessment,
                "missing": self.missing, "issues": self.issues, "claims": self.claims}


def assess(ticket: Ticket | None, retrieval: Retrieval, today: date) -> Evidence:
    result = Evidence(ticket.id if ticket else None, ticket.version if ticket else None, today,
                      ticket.purchase_date if ticket else None, ticket.premium_exports_used if ticket else None,
                      issues=list(retrieval.issues))
    refund = next((d for d in retrieval.documents if topic(d) == "refund"), None)
    requires_exports = excludes_enterprise = False
    if refund:
        result.window_days, requires_exports, excludes_enterprise = refund_terms(refund)
        if result.window_days is not None:
            condition = " No premium exports may have been used." if requires_exports else ""
            condition += " Enterprise plans are excluded." if excludes_enterprise else ""
            result.claims.append({"document_id": refund.id, "topic": "refund",
                                  "text": f"The current refund policy has a window of {result.window_days} calendar days from the first payment.{condition} Verification is required before confirming a refund."})
        else:
            result.issues.append("unsupported_refund_policy_language")
    for document in retrieval.documents:
        kind = topic(document)
        text = document.text.lower()
        if kind == "billing" and all(word in text for word in ("purchase age", "premium-export", "prior credits")):
            result.claims.append({"document_id": document.id, "topic": kind,
                                  "text": "Billing verification requires purchase age, premium export usage and prior credits. Missing evidence requires clarification or escalation."})
        elif kind == "cancellation" and "stops future renewals" in text and "does not automatically authorize a refund" in text:
            result.claims.append({"document_id": document.id, "topic": kind,
                                  "text": "Cancellation stops future renewals and does not automatically authorize a refund. Subscription changes require explicit approval."})
        elif kind == "escalation" and "billing cases lacking verified records" in text:
            result.claims.append({"document_id": document.id, "topic": kind,
                                  "text": "Billing cases lacking verified records require escalation."})
    if ticket is None:
        result.assessment = "policy_only" if result.claims and not result.issues else "insufficient_evidence"
        return result
    if ticket.purchase_date is None:
        result.missing.append("purchase_date")
    else:
        result.elapsed_days = (today - ticket.purchase_date).days
    if ticket.premium_exports_used is None:
        result.missing.append("premium_exports_used")
    if ticket.prior_credits is None:
        result.missing.append("prior_credits")
    if excludes_enterprise and not ticket.plan:
        result.missing.append("plan_entitlement")
    if result.window_days is None or result.issues or result.elapsed_days is None or result.elapsed_days < 0:
        if result.elapsed_days is not None and result.elapsed_days < 0:
            result.issues.append("future_purchase_date")
        result.assessment = "insufficient_evidence"
    elif (result.elapsed_days > result.window_days or
          (requires_exports and ticket.premium_exports_used is not None and ticket.premium_exports_used > 0) or
          (ticket.prior_credits is not None and ticket.prior_credits > 0) or
          (excludes_enterprise and ticket.plan and ticket.plan.lower() == "enterprise")):
        result.assessment = "manual_escalation"
    else:
        result.assessment = "verification_required"
    return result

