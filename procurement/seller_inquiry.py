"""Product questions in the Chatbot → an optional WhatsApp message to the seller.

While an employee is still describing a request (before Confirm), they may ask
something the catalogue can't answer ("does it have USB-C?", "is the stand
height-adjustable?"). The Chatbot recognises that, gives a short general answer,
and offers to contact the seller. If the employee agrees, they edit the message
themselves and press Send; only then is it POSTed to the seller-contact routine
(the same webhook as the Contact Sellers page) as a ``seller_inquiry`` event.

Every inquiry goes to one number for the demo: ``SELLER_INQUIRY_PHONE``
(default +85291465098). The routine does the actual WhatsApp send.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .tools.seller_contact import ContactResult, contact_configured, post_event

DEFAULT_PHONE = "+85291465098"
PHONE_ENV = "SELLER_INQUIRY_PHONE"

_QUESTION_START = re.compile(
    r"^\s*(what|which|does|do|did|is|are|can|could|how|why|will|would|should|"
    r"tell me|explain|compare|difference|any idea|i wonder|wondering)\b",
    re.IGNORECASE,
)

CLASSIFY_PROMPT = """You help employees of a company buy office equipment.

Decide whether the employee's message is asking for MORE KNOWLEDGE ABOUT A PRODUCT
(features, specifications, compatibility, materials, comparisons between models,
warranty or after-sales details, availability, what a spec means) rather than
(a) giving purchase-request details (quantity, budget, deadline, specs they need),
or (b) asking how to fill in the request.

Current request draft (may be empty): {draft}
Employee message: {message}

Return JSON with:
- product_question: true or false
- product: the product or model they are asking about, or null
- reply: if product_question is true, one or two sentences of general guidance in
  plain English: what the feature means or what to look for. Never claim that a
  specific model has or lacks a feature, and never state stock, price or warranty;
  those are for the seller to confirm. Otherwise null."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "product_question": {"type": "boolean"},
        "product": {"type": ["string", "null"]},
        "reply": {"type": ["string", "null"]},
    },
    "required": ["product_question", "product", "reply"],
}


def seller_phone() -> str:
    return os.getenv(PHONE_ENV, DEFAULT_PHONE)


def looks_like_question(text: str) -> bool:
    """Cheap pre-filter so ordinary request messages cost no extra model call."""
    text = (text or "").strip()
    return bool(text) and ("?" in text or bool(_QUESTION_START.match(text)))


@dataclass(frozen=True)
class ProductQuestion:
    product: str | None
    reply: str | None


def classify(message: str, draft_summary: str = "", llm=None) -> ProductQuestion | None:
    """Return a ProductQuestion when the message asks about a product, else None.

    Never raises: if the model can't be reached, the Chatbot carries on as before.
    """
    if not looks_like_question(message):
        return None

    async def _run() -> dict:
        from .llm.adapter import ConfigurableLLM

        model = llm or ConfigurableLLM()
        try:
            return await model.structured(
                CLASSIFY_PROMPT.format(draft=draft_summary or "(none)", message=message),
                CLASSIFY_SCHEMA,
            )
        finally:
            client = getattr(model, "_client", None) if llm is None else None
            if client is not None:
                await client.close()  # close inside this loop, not after it

    try:
        data = asyncio.run(_run())
    except Exception:
        return None
    if not data.get("product_question"):
        return None
    return ProductQuestion(product=data.get("product") or None, reply=data.get("reply") or None)


def default_message(question: str, *, employee: str, department: str | None,
                    product: str | None) -> str:
    who = f"{employee}" + (f" from the {department} team" if department else "")
    about = f" about {product}" if product else ""
    body = question.strip() or "[Type your question here]"
    return (
        f"Hello, this is {who}. We're considering a purchase and have a question{about}:\n\n"
        f"{body}\n\n"
        "Could you let us know? Thank you."
    )


def send_inquiry(message: str, *, employee: str, department: str | None,
                 product: str | None, chat_ref: str | None = None) -> ContactResult:
    """POST a ``seller_inquiry`` event; the routine sends ``message`` to ``to``."""
    inquiry_id = f"INQ-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "event": "seller_inquiry",
        "inquiry_id": inquiry_id,
        "to": seller_phone(),
        "message": message.strip(),
        "from": employee,
        "department": department,
        "product": product,
        "chat": chat_ref,
    }
    return post_event(payload, inquiry_id)


__all__ = [
    "ProductQuestion", "classify", "contact_configured", "default_message",
    "looks_like_question", "send_inquiry", "seller_phone",
]
