"""Render verified evidence only. This boundary grants no tool/state capability."""
import json
import time
from dataclasses import replace
from typing import Protocol

import anyio
import httpx

from .errors import AssistantError
from .retrieval import Evidence
from .tracing import Trace

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


class AvalAIAdapter:
    mode = "avalai"
    model = "deepseek-v4.1-flash"
    endpoint = "https://api.avalai.ir/v1/chat/completions"

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self._api_key = api_key
        self._transport = transport

    async def render(self, evidence: Evidence, preferences: dict[str, str], *,
                     draft: bool = False, trace: Trace | None = None) -> str:
        concise = preferences.get("length") == "concise"
        if draft:
            # Customer wording can include the effect of cancellation, but not
            # the internal approval procedure appended to the verified claim.
            claims = [{"topic": claim["topic"],
                       "text": claim["text"].removesuffix(" Subscription changes require explicit approval.")}
                      for claim in evidence.claims if claim["topic"] in _CUSTOMER_TOPICS]
            evidence = replace(evidence, claims=claims)
            required = DeterministicAdapter._customer(evidence, preferences, True)
            openings = ([] if concise or preferences.get("introductions") == "omit"
                        else ["Thank you for contacting support.", "Thank you for reaching out about your request."])
            closings = [] if concise else ["Thank you for your patience.", "We appreciate your patience during the review."]
        else:
            required = DeterministicAdapter._operator(evidence, concise)
            openings = [] if concise else ["Here is what the verified records show.", "Here is a summary of the available evidence."]
            closings = []
        facts = evidence.public()
        del facts["ticket_version"]
        data = {"audience": "customer" if draft else "operator", "evidence": facts, "preferences": preferences,
                "required_passages": required, "optional_openings": openings, "optional_closings": closings}
        messages = [
            {"role": "system", "content": (
                "Compose an English support response from the JSON data. Use every required_passage exactly "
                "once, verbatim, on its own line. Arrange the passages in a readable order for the audience. "
                "You may include one optional_opening first and one optional_closing last, also verbatim. "
                "Use '- ' before every nonblank line only if preferences.format is bullets. "
                "Return the response only, without any other text, headings, markup, or claims."
            )},
            {"role": "user", "content": json.dumps(data)},
        ]
        metadata = {"mode": self.mode, "provider": self.mode, "model": self.model,
                    "assessment": evidence.assessment}
        started = time.perf_counter()
        if trace is not None:
            trace.model_adapter = self.mode
        try:
            answer = await self._complete(messages, max_tokens=600, metadata=metadata)
            lines = [line for line in answer.split("\n") if line.strip()]
            if preferences.get("format") == "bullets":
                if any(not line.startswith("- ") for line in lines):
                    raise AssistantError("avalai_unsafe_output")
                lines = [line[2:] for line in lines]
            if lines and lines[0] in openings:
                lines.pop(0)
            if lines and lines[-1] in closings:
                lines.pop()
            if len(answer) > 2000 or sorted(lines) != sorted(required):
                raise AssistantError("avalai_unsafe_output")
            return answer
        except AssistantError as exc:
            metadata["error_code"] = exc.code
            raise
        finally:
            if trace is not None:
                trace.event("model.render", latency_ms=round((time.perf_counter() - started) * 1000, 3), **metadata)

    async def _complete(self, messages: list[dict], *, max_tokens: int, metadata: dict) -> str:
        payload = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                   "stream": False, "thinking": {"type": "disabled"}}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            for attempt in (1, 2):
                metadata["attempts"] = attempt
                try:
                    with anyio.fail_after(16):
                        response = await client.post(self.endpoint, headers=headers, json=payload)
                except (TimeoutError, httpx.TimeoutException):
                    metadata["request_status"] = "timeout"
                    error = AssistantError("avalai_timeout", 504)
                except (httpx.NetworkError, httpx.RemoteProtocolError):
                    metadata["request_status"] = "network_error"
                    error = AssistantError("avalai_unavailable", 503)
                except (httpx.HTTPError, UnicodeError):
                    metadata["request_status"] = "request_error"
                    raise AssistantError("avalai_request_rejected") from None
                else:
                    status = metadata["request_status"] = response.status_code
                    if response.is_success:
                        return self._parse(response, metadata)
                    if status in (401, 403):
                        raise AssistantError("avalai_authentication_failed")
                    if status == 429:
                        error = AssistantError("avalai_rate_limited", 503)
                    elif status in (408, 500, 502, 503, 504):
                        error = AssistantError("avalai_unavailable", 503)
                    else:
                        raise AssistantError("avalai_request_rejected")
                if attempt == 1:
                    await anyio.sleep(0.25)
            raise error

    @staticmethod
    def _parse(response: httpx.Response, metadata: dict) -> str:
        try:
            payload = response.json()
            usage = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(usage, dict):
                counts = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                          if type(usage.get(key)) is int and usage[key] >= 0}
                if counts:
                    metadata["usage"] = counts
            choice = payload["choices"][0]
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            metadata["finish_reason"] = reason if reason in ("stop", "length", "content_filter", "tool_calls") else "other"
            message = choice["message"]
            content = message["content"]
            if (reason != "stop" or message.get("tool_calls")
                    or message.get("function_call") or not isinstance(content, str) or not content.strip()):
                raise ValueError
        except (ValueError, TypeError, KeyError, IndexError):
            raise AssistantError("avalai_invalid_response") from None
        return content.strip().replace("\r\n", "\n")
