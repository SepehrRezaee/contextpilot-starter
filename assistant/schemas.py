import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from .errors import AssistantError

Tenant = Literal["northstar", "harbor"]
# Single registry of tenants and their ticket namespaces (fixture metadata, not prose).
TENANTS: dict[str, str] = {"northstar": "NS", "harbor": "HB"}
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
TicketID = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}-[1-9][0-9]{0,6}$")]
BodyHash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: Tenant
    user_id: Identifier
    session_id: Identifier


class RespondRequest(Context):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ApproveRequest(Context):
    body_hash: BodyHash


class Ticket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tenant_id: Tenant
    id: TicketID
    version: Annotated[StrictInt, Field(ge=1)]
    status: str
    subject: str
    body: str
    purchase_date: date | None = None
    premium_exports_used: Annotated[StrictInt, Field(ge=0)] | None = None
    prior_credits: Annotated[StrictInt, Field(ge=0)] | None = None
    plan: str | None = None
    comments: list[dict] = Field(default_factory=list)


class Document(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,80}$")]
    tenant_id: str
    title: str
    text: str
    effective_at: date
    status: str


def validate_ticket_scope(tenant: str, ticket_id: str) -> None:
    if not re.fullmatch(r"[A-Z]{2,5}-[1-9][0-9]{0,6}", ticket_id):
        raise AssistantError("invalid_ticket_id", 422)
    # Namespace metadata from the supplied fixture; identity is never read from prose.
    prefix = TENANTS.get(tenant)
    if prefix is None or not ticket_id.startswith(prefix + "-"):
        raise AssistantError("ticket_scope", 403)

