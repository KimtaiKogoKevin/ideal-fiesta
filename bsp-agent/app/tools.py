"""The agent's tools: plain Python functions over the JSON data, plus the
JSON-schema definitions Claude sees.

Nothing here calls Claude. These run locally when Claude asks for them.
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

PRODUCTS = json.loads((DATA / "products.json").read_text())
ORDERS = json.loads((DATA / "orders.json").read_text())
BY_SKU = {p["sku"]: p for p in PRODUCTS}

CATEGORIES = sorted({p["category"] for p in PRODUCTS})
BRANDS = sorted({p["brand"] for p in PRODUCTS})

# Words that carry no signal for matching a part.
STOPWORDS = {
    "a", "an", "the", "for", "and", "or", "with", "of", "to", "in", "on", "my",
    "our", "is", "it", "i", "we", "need", "which", "what", "do", "new",
    "replacement", "shower", "valve", "part",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9.\-]*", text.lower()) if t not in STOPWORDS]


def _norm(s: str) -> str:
    """Part numbers are written inconsistently (CR101C vs CR-101C), so compare without punctuation."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _summary(p: dict) -> dict:
    """The compact view returned in search results (keeps the context small)."""
    return {
        "sku": p["sku"],
        "name": p["name"],
        "brand": p["brand"],
        "category": p["category"],
        "part_number": p["part_number"],
        "price_inc_vat": p["price_inc_vat"],
        "attributes": p["attributes"],
    }


def search_catalogue(query: str, brand: str | None = None, category: str | None = None,
                     attributes: dict | None = None, limit: int = 8) -> dict:
    """Keyword search with optional hard filters, ranked by a simple score.

    Scoring: each query word found in the product name/series scores 1, an exact
    part-number match scores 10 (customers often quote one), and each matching
    attribute filter scores 2. Attribute filters are soft (they rank, not exclude)
    because many products have no parsed attributes at all.
    """
    q_tokens = _tokens(query)
    q_norm = _norm(query)
    results = []
    for p in PRODUCTS:
        if brand and p["brand"].lower() != brand.lower():
            continue
        if category and p["category"] != category:
            continue
        haystack = " ".join([p["name"], p["brand"], json.dumps(p["attributes"])]).lower()
        score = sum(1 for t in q_tokens if t in haystack)
        pn = _norm(p["part_number"])
        if pn and (pn in q_norm or any(_norm(t) == pn for t in q_tokens)):
            score += 10
        for k, v in (attributes or {}).items():
            have = str(p["attributes"].get(k, "")).lower()
            if have and str(v).lower() in have:
                score += 2
        if score > 0 or not q_tokens:
            results.append((score, p))
    results.sort(key=lambda r: -r[0])
    hits = [_summary(p) | {"match_score": s} for s, p in results[:limit]]
    return {"count": len(results), "showing": len(hits), "results": hits}


def get_product(sku: str) -> dict:
    p = BY_SKU.get(sku)
    if not p:
        # Be forgiving: accept a manufacturer part number too.
        p = next((x for x in PRODUCTS if _norm(x["part_number"]) == _norm(sku)), None)
    return p or {"error": f"No product with SKU or part number '{sku}'."}


PRIVACY_BLOCK = ("This order is not registered to the sender's email address, so its details can't be "
                 "shared in the reply. Tell the customer the team will verify their identity and follow up, "
                 "and set needs_human.")


def get_order(order_id: str | None = None, email: str | None = None, sender: str | None = None) -> dict:
    """Look up orders, but only ever return ones that belong to the email's sender.

    This is enforced here in code, not left to the prompt: an email saying
    "check order BSP104203" or "look up jane@example.com" can't reveal another
    customer's order, however it is worded. (sender=None, e.g. a direct Python
    call, skips the check.)
    """
    owner = (sender or "").strip().lower()

    def mine(o: dict) -> bool:
        return not sender or o["email"].lower() == owner

    if order_id:
        o = next((o for o in ORDERS if o["order_id"].lower() == order_id.strip().lower()), None)
        if not o:
            return {"error": f"No order found with ID {order_id}."}
        return o if mine(o) else {"error": PRIVACY_BLOCK, "order_exists": True}
    if email:
        if sender and email.strip().lower() != owner:
            return {"error": "For privacy, orders can only be looked up for the sender's own email address."}
        found = [o for o in ORDERS if o["email"].lower() == email.strip().lower()]
        return {"orders": found} if found else {"error": f"No orders found for {email}."}
    return {"error": "Provide order_id or email."}


# --- Tool definitions sent to Claude ----------------------------------------
# strict=True makes the API guarantee the inputs match these schemas.

INTENTS = ["part_identification", "order_status", "trade_quote", "return_exchange",
           "damaged_item", "complaint", "invoice_request", "general_question"]

TOOLS = [
    {
        "name": "search_catalogue",
        "description": (
            "Search the Bathroom Spare Parts catalogue. Returns up to 8 products ranked by match. "
            "Put brand, model/series names, part numbers and descriptive words in `query`. "
            "Use `brand`/`category` as hard filters only when sure. `attributes` softly boosts "
            "products whose parsed attributes match, e.g. {\"system\": \"combi\"}, "
            "{\"rotation\": \"clockwise close\"}, {\"colour_code\": \"pink\"}, {\"finish\": \"chrome\"}. "
            "Search more than once with different wording if the first results look wrong."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "brand": {"anyOf": [{"type": "string", "enum": BRANDS}, {"type": "null"}]},
                "category": {"anyOf": [{"type": "string", "enum": CATEGORIES}, {"type": "null"}]},
                "attributes": {"anyOf": [{"type": "null"}, {
                    "type": "object",
                    "properties": {
                        "system": {"type": "string"},
                        "rotation": {"type": "string"},
                        "colour_code": {"type": "string"},
                        "series": {"type": "string"},
                        "finish": {"type": "string"},
                        "size_mm": {"type": "string"},
                        "date_constraint": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                }]},
            },
            "required": ["query", "brand", "category", "attributes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product",
        "description": "Get full details (price, URL, attributes) for one product by SKU (e.g. BSP-0021) or manufacturer part number.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_order",
        "description": (
            "Look up a customer order by order ID (format BSP######). If the email has no order ID, "
            "pass the sender's email address instead to list their orders."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
            },
            "required": ["order_id", "email"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_customer",
        "description": (
            "Record a clarifying question to include in the reply when you cannot be sure which part "
            "the customer needs (e.g. water system, rotation direction, install date, a photo of a label). "
            "Call once per question. It does not send anything."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "draft_reply",
        "description": (
            "Finish by drafting the reply email for a staff member to review. Always call this exactly "
            "once, as your final step. Nothing is sent automatically."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "The full reply email, friendly and concise, UK English."},
                "intent": {"type": "string", "enum": INTENTS},
                "recommended_skus": {
                    "type": "array", "items": {"type": "string"},
                    "description": "SKUs recommended or referenced, best match first, max 3.",
                },
                "order_id": {"type": ["string", "null"], "description": "The order this email is about, if any."},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "urgency": {"type": "string", "enum": ["high", "normal"]},
                "needs_human": {"type": "boolean"},
                "internal_note": {
                    "type": "string",
                    "description": "Short note for staff: why, what to check, any action they must take (refund, swap, trade price).",
                },
            },
            "required": ["body", "intent", "recommended_skus", "order_id", "confidence",
                         "urgency", "needs_human", "internal_note"],
            "additionalProperties": False,
        },
    },
]


PRICE_RE = re.compile(r"£\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)")
URL_RE = re.compile(r"https?://[^\s<>()\"']+")
SITE_ROOTS = {"https://www.bathroomspareparts.co.uk", "https://www.bathroomspareparts.co.uk/",
              "https://bathroomspareparts.co.uk", "https://bathroomspareparts.co.uk/"}
ALL_URLS = {p["url"] for p in PRODUCTS if p.get("url")}


def _collect(obj, prices: set, urls: set, skus: set, orders: set):
    """Walk tool outputs and note every price, URL, SKU and order ID the agent actually saw."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and ("price" in k or "total" in k):
                prices.add(round(float(v), 2))
            elif isinstance(v, str):
                if k == "url":
                    urls.add(v)
                elif k == "sku":
                    skus.add(v)
                elif k == "order_id":
                    orders.add(v.upper())
            _collect(v, prices, urls, skus, orders)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, prices, urls, skus, orders)


def verify_draft(draft: dict, tool_outputs: list) -> list[str]:
    """Check the draft against the data the agent looked up. Returns a list of problems (empty = all good).

    Catches the model inventing things: a SKU that doesn't exist, a price or link
    that isn't in the catalogue, or an order it never successfully looked up.
    """
    prices, urls, skus, orders = set(), set(), set(), set()
    _collect(tool_outputs, prices, urls, skus, orders)
    for sku in draft.get("recommended_skus") or []:
        if sku in BY_SKU:  # recommended products' own prices and links are always valid
            p = BY_SKU[sku]
            prices |= {round(p["price_inc_vat"], 2), round(p["price_ex_vat"], 2)}
            urls.add(p["url"])

    issues = []
    for sku in draft.get("recommended_skus") or []:
        if sku not in BY_SKU:
            issues.append(f"Recommended SKU {sku} is not in the catalogue.")
        elif sku not in skus:
            issues.append(f"Recommended SKU {sku} was never looked up during this run.")
    body = draft.get("body") or ""
    for m in PRICE_RE.finditer(body):
        value = round(float(m.group(1).replace(",", "")), 2)
        if value not in prices:
            issues.append(f"Price £{m.group(1)} in the reply doesn't match any looked-up product or order.")
    for u in URL_RE.findall(body):
        u = u.rstrip(".,;:!?")
        if u not in urls and u not in ALL_URLS and u not in SITE_ROOTS:
            issues.append(f"Link {u} in the reply isn't a known product page.")
    oid = (draft.get("order_id") or "").upper()
    if oid and oid not in orders:
        issues.append(f"Order {oid} was not successfully looked up for this sender.")
    return issues


def run_tool(name: str, args: dict, state: dict) -> dict:
    """Dispatch one tool call. `state` holds the sender and collects questions across the run."""
    if name == "search_catalogue":
        return search_catalogue(args.get("query", ""), args.get("brand"), args.get("category"),
                                {k: v for k, v in (args.get("attributes") or {}).items() if v})
    if name == "get_product":
        return get_product(args["sku"])
    if name == "get_order":
        return get_order(args.get("order_id"), args.get("email"), sender=state.get("sender") or "")
    if name == "ask_customer":
        state.setdefault("questions", []).append(args["question"])
        return {"ok": True, "note": "Question recorded. Include it in the reply body."}
    return {"error": f"Unknown tool {name}"}
