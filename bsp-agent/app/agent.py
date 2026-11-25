"""The agent loop: send the email to Claude with the tools, run whatever tools
Claude asks for, send the results back, repeat until Claude calls draft_reply.

Written as a generator that yields one event per step, so the web UI can show
each tool call live and the eval script can consume the same events.
"""
import json
import os
import time
from pathlib import Path

import anthropic

from .tools import TOOLS, run_tool, verify_draft

# Fallback location for the API key when ANTHROPIC_API_KEY isn't set.
KEY_FILE = Path.home() / ".config" / "bsp-agent" / "anthropic_key"

MODEL = os.getenv("BSP_MODEL", "claude-sonnet-5-5")
EFFORT = os.getenv("BSP_EFFORT", "medium")
MAX_STEPS = int(os.getenv("BSP_MAX_STEPS", "10"))
# The demo data is dated around 24 Nov 2026, so "today" is pinned for consistent answers.
TODAY = os.getenv("BSP_TODAY", "Tuesday 24 November 2026")

_client = None


def _load_api_key() -> str:
    """ANTHROPIC_API_KEY env var first, then ~/.config/bsp-agent/anthropic_key.

    The key is only passed to the SDK client. It is never printed, logged or
    included in error messages.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        key = ""
    if not key:
        raise RuntimeError(f"No API key: set ANTHROPIC_API_KEY or put the key in {KEY_FILE}")
    return key


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=_load_api_key())
    return _client


SYSTEM_PROMPT = """You are the customer-service assistant for Bathroom Spare Parts (bathroomspareparts.co.uk), a family-run UK retailer of bathroom spare parts based in Hoddesdon, Hertfordshire. You read one customer email at a time, use your tools to research it, and draft a reply for a staff member to approve. You never send emails, place orders, issue refunds or promise anything staff must authorise; you draft and flag.

How to work:
1. Work out what the customer wants (intent) and how urgent it is.
2. Research with the tools. Look up any order mentioned; if the customer clearly refers to an order but gives no ID, look up their email address. Search the catalogue for any part they need, ask about, or have a problem with.
3. Finish by calling draft_reply exactly once.

Identifying parts: many cartridges look alike and are only told apart by details, so match on these:
- Brand and valve model/series (e.g. Aqualisa Midas 200, Burlington Stour BF3S, Bristan Prism).
- Water system. Combi boiler or unvented cylinder = mains/high pressure. A cold tank in the loft with a hot cylinder and no pump = gravity/low pressure. Some brands colour-code cartridges by system (e.g. Aqualisa pink/green/orange); read the product names to map colour to system.
- Rotation: which way the handle turns to close (clockwise close vs anti-clockwise close).
- Install date when the product has date-dependent versions (pre/post a given year).
- Size in mm and number of functions/outlets for diverters; finish and series for flush plates.
Search more than once with different wording when results are thin. If you are confident, recommend one part. If you can narrow it to a brand and type but not one exact part, still recommend the 3 most likely candidates (best first, confidence "low" or "medium") so staff and customer have a starting point, and use ask_customer for the detail that would settle it. Only if you can't even tell the brand (no markings, no brand named) should you recommend nothing; then say so honestly and ask for what would identify it (brand on the valve, measurements, a photo of the valve body or any label).

recommended_skus: list the products the email is about, best first, max 3. For returns, exchanges and damage this means the item the customer has (from their order) plus the correct replacement if it differs.

When a customer's photo is attached, describe what you can see that matters for identification and use it as evidence, but don't overstate certainty from a photo alone.

Policies to apply in drafts (staff confirm the details):
- Returns: unused, unfitted items can be returned or exchanged; tell them staff will confirm the return arrangements. For a wrong-part exchange, identify the correct replacement.
- Damaged or faulty on arrival: apologise, ask them to keep the packaging, and say staff will arrange a replacement. Needs a human.
- Trade or bulk quotes: never invent trade prices. Confirm the parts and quantities and say the trade team will send a quote. Needs a human.
- Invoices: say the VAT invoice will be emailed by the accounts team. Needs a human.
- Delivery/order status: give the real status, carrier and tracking from the order. If an order is awaiting stock, say so plainly with the expected date.
- Never make up prices, stock, delivery costs or policies you weren't given. If you don't know (e.g. international shipping costs), say the team will confirm and set needs_human.

Urgency is "high" when a customer has no working shower/toilet, a tradesperson has a job booked imminently, or the customer is angry or threatening a refund/complaint. Otherwise "normal".

needs_human is true whenever staff must do something (refund, exchange, replacement, quote, invoice, unknown answer) or the customer is upset. It is false only for a reply that can safely go out as written after a quick read.

Intent labels: part_identification (which part do I need), order_status (where is my order / will it ship), trade_quote (trade or bulk pricing), return_exchange (send back or swap something), damaged_item (arrived damaged or faulty), complaint (angry about service), invoice_request, general_question (anything else). Pick the customer's main purpose; an angry chase about a late order is a complaint.

Writing the reply: UK English, warm, plain and brief. Address the customer by first name if known. Reference parts by name and manufacturer part number, with the price inc. VAT and the product URL. Include any clarifying questions you recorded. Sign off as "The Bathroom Spare Parts Team". Use plain text, no markdown."""


def _format_email(email: dict) -> str:
    lines = [
        f"Today is {TODAY}.",
        "",
        f"From: {email.get('from') or 'unknown'}",
        f"Subject: {email.get('subject') or '(no subject)'}",
        f"Received: {email.get('received_at') or 'just now'}",
        "",
        email.get("body", ""),
    ]
    att = email.get("attachment")
    if att and att.get("description"):
        # The sample emails carry a text description of their photo instead of a real image.
        lines += ["", f"[Photo attached. Description of the photo: {att['description']}]"]
    return "\n".join(lines)


def _block_to_dict(block) -> dict:
    return block.model_dump(exclude_none=True)


def run_agent(email: dict, image: tuple[bytes, str] | None = None):
    """Yield events: {"type": "tool_call"|"text"|"final"|"error"|"usage", ...}.

    image is optional (raw bytes, media type) for real photo uploads.
    """
    import base64

    content = []
    if image:
        data, media_type = image
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type,
            "data": base64.standard_b64encode(data).decode()}})
    content.append({"type": "text", "text": _format_email(email)})
    messages = [{"role": "user", "content": content}]

    # The sender comes from the email itself, never from the model, so order lookups can be checked against it.
    state: dict = {"sender": email.get("from") or ""}
    tool_outputs: list = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "api_calls": 0}
    started = time.time()
    nudged = False

    for _ in range(MAX_STEPS):
        try:
            resp = client().beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                # The system prompt and tools never change, so cache them.
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages,
                output_config={"effort": EFFORT},
                # If a safety classifier declines, the API retries on a fallback model in the same call.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIError as e:
            yield {"type": "error", "message": f"Claude API error: {e}"}
            return
        except RuntimeError as e:  # missing API key; the message names the file, never the key
            yield {"type": "error", "message": str(e)}
            return

        usage["api_calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            usage[k] += getattr(resp.usage, k, 0) or 0

        if resp.stop_reason == "refusal":
            yield {"type": "error", "message": "The model declined this request."}
            return

        # Keep the full assistant content (including thinking blocks) in the history.
        messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in resp.content]})

        for b in resp.content:
            if b.type == "text" and b.text.strip():
                yield {"type": "text", "text": b.text}

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            if nudged:
                yield {"type": "error", "message": "Agent stopped without drafting a reply."}
                return
            nudged = True
            messages.append({"role": "user", "content": "Please finish by calling draft_reply."})
            continue

        results = []
        final = None
        for tu in tool_uses:
            if tu.name == "draft_reply":
                final = dict(tu.input)
                final["clarifying_questions"] = state.get("questions", [])
                yield {"type": "tool_call", "tool": tu.name, "input": tu.input, "output": {"ok": True}}
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "Draft saved."})
                continue
            out = run_tool(tu.name, tu.input, state)
            tool_outputs.append(out)
            yield {"type": "tool_call", "tool": tu.name, "input": tu.input, "output": out}
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(out), "is_error": "error" in out})

        if final:
            # Guardrail: check every SKU, price, link and order ID in the draft against what was looked up.
            issues = verify_draft(final, tool_outputs)
            final["checks"] = {"passed": not issues, "issues": issues}
            if issues:
                final["needs_human"] = True
            yield {"type": "check", "passed": not issues, "issues": issues}
            usage["seconds"] = round(time.time() - started, 1)
            usage["model"] = resp.model
            yield {"type": "usage", **usage}
            yield {"type": "final", "draft": final}
            return
        messages.append({"role": "user", "content": results})

    yield {"type": "error", "message": f"Agent hit the {MAX_STEPS}-step limit without drafting a reply."}
