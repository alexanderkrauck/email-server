"""Shared request models used by HTTP and MCP adapters."""

from pydantic import BaseModel, EmailStr, Field


class SendMailInput(BaseModel):
    account_id: int
    to_addresses: list[EmailStr]
    subject: str = Field(max_length=998)
    body_text: str | None = None
    body_html: str | None = None
    cc_addresses: list[EmailStr] = Field(default_factory=list)
    bcc_addresses: list[EmailStr] = Field(default_factory=list)
    reply_to: EmailStr | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)
