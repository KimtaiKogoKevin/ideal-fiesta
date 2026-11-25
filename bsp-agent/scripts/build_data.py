"""Build the demo datasets for the Bathroom Spare Parts enquiry agent.

Outputs (in ../data):
  products.json         – real public catalogue sample, enriched with brand/type/attributes
  orders.json           – FICTIONAL orders referencing real products
  emails.json           – FICTIONAL customer emails with ground-truth labels (for evaluation)
  data_quality.json     – issues found in the public catalogue (bonus finding)
"""
import json, re, random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from raw_products import RAW, BASE

OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)
random.seed(42)

BRANDS = ["Armitage Shanks", "Better Bathrooms", "Chartley Stone", "B&Q Good Home",
          "Abacus", "Adora", "Alpi", "Aqata", "Aqualisa", "Bathstore", "Bristan",
          "Burlington", "Cassellie", "Cifial", "Geberit"]

def brand_of(name):
    for b in BRANDS:
        if name.lower().startswith(b.lower()) or name.lower().startswith(b.split()[0].lower() + " "):
            return b
    return name.split()[0]

def attributes(name, category):
    n = name.lower()
    a = {}
    if "anti-clockwise close" in n: a["rotation"] = "anti-clockwise close"
    elif "clockwise close" in n: a["rotation"] = "clockwise close"
    elif "clockwise open" in n: a["rotation"] = "clockwise open"
    for p in ["high pressure", "low pressure", "gravity", "combi"]:
        if p in n: a["system"] = p
    for c in ["pink", "green", "orange", "grey"]:
        if re.search(rf"\b{c}\b", n): a["colour_code"] = c
    for f in ["bright chrome", "gloss chrome", "matt chrome", "alpine white", "glossy white",
              "white", "matt black", "black chrome", "brass", "red gold"]:
        if f in n: a["finish"] = f; break
    m = re.search(r"(\d+)\s?mm", n)
    if m: a["size_mm"] = int(m.group(1))
    m = re.search(r"\b(alpha 01|delta ?\d+|kappa \d+|200f|highline|type 70|midas \d+|opto|aquarian|dream|futori|planet|fusion|metro|artisan|prism|1901|glorious|totem|quatra|blade|eden|stour|clyde|arcade|edwardian)\b", n)
    if m: a["series"] = m.group(1).title()
    m = re.search(r"\b(pre|from|up to|post)[ -](january |march |may )?(\d{4})", n)
    if m: a["date_constraint"] = m.group(0)
    if "dual flush" in n: a["flush"] = "dual"
    return a

products = []
for i, (cat, name, pn, price, path) in enumerate(RAW, start=1):
    products.append({
        "sku": f"BSP-{i:04d}",
        "name": name,
        "brand": brand_of(name),
        "category": cat,
        "part_number": pn,
        "price_inc_vat": price,
        "price_ex_vat": round(price / 1.2, 2),
        "url": BASE + path,
        "attributes": attributes(name, cat),
    })
by_pn = defaultdict(list)
for p in products:
    by_pn[p["part_number"].replace(" ", "").replace("-", "").upper()].append(p)

# ---------------- Data quality report ----------------
issues = []
for key, ps in by_pn.items():
    if len(ps) > 1:
        prices = {p["price_inc_vat"] for p in ps}
        brands = {p["brand"] for p in ps}
        kind = "same_part_number_different_price" if len(prices) > 1 else "duplicate_listing_same_part"
        if len(brands) > 1: kind = "same_part_number_multiple_brands"
        issues.append({"type": kind, "part_number": ps[0]["part_number"],
                       "listings": [{"sku": p["sku"], "name": p["name"], "price": p["price_inc_vat"], "url": p["url"]} for p in ps]})
TYPOS = {"thermostaic": "thermostatic", "flush pate": "flush plate"}
for p in products:
    for bad, good in TYPOS.items():
        if bad in p["name"].lower():
            issues.append({"type": "typo_in_title", "sku": p["sku"], "name": p["name"], "found": bad, "suggest": good, "url": p["url"]})
# Cross-brand part-number reuse spotted on the live site (Adora SP584 vs Burlington SP584)
issues.append({"type": "note", "detail": "Part number SP584 is listed under both Adora (MB1500RV) and Burlington (TF2S/TF3S) flow cartridges on the live site – worth confirming they are the same OEM part."})

# ---------------- Fictional orders ----------------
def sku_for(pn):
    return next(p for p in products if p["part_number"] == pn)

today = datetime(2026, 11, 24)
customers = [
    ("Sarah Whitfield", "sarah.whitfield@example.com", "St Albans AL1"),
    ("Tom Okafor", "tom.okafor@example.com", "Luton LU2"),
    ("Priya Nair", "priya.nair@example.com", "Enfield EN2"),
    ("Gareth Hughes", "gareth@hughes-plumbing.example.com", "Watford WD17"),
    ("Linda Marsh", "linda.marsh@example.com", "Ware SG12"),
    ("James Patel", "j.patel@example.com", "Harlow CM20"),
    ("Emma Clarke", "emma.clarke@example.com", "Cheshunt EN8"),
    ("Mike Brennan", "mike@brennanbathrooms.example.com", "Stevenage SG1"),
    ("Aisha Rahman", "aisha.r@example.com", "Hertford SG14"),
    ("David Lowe", "dlowe@example.com", "Welwyn AL6"),
]
order_specs = [
    # (customer_idx, [(pn, qty)], days_ago, status, carrier)
    (0, [("022802", 1)], 2, "dispatched", "DPD"),
    (1, [("115.035.11.1", 1)], 1, "processing", None),
    (2, [("SP711", 2)], 6, "delivered", "Royal Mail"),
    (3, [("SPACW0005", 4), ("SP33", 4)], 3, "dispatched", "DPD"),
    (4, [("518113", 1)], 9, "delivered", "DPD"),
    (5, [("ART.PT.21", 1)], 4, "awaiting_stock", None),
    (6, [("115.125.21.1", 1)], 5, "delivered", "Royal Mail"),
    (7, [("CR101C", 2), ("CR024", 2)], 1, "processing", None),
    (8, [("MB1113", 1)], 12, "delivered", "Royal Mail"),
    (9, [("265509", 1)], 3, "dispatched", "DPD"),
]
orders = []
for n, (ci, lines, ago, status, carrier) in enumerate(order_specs, start=1):
    name, email, area = customers[ci]
    placed = today - timedelta(days=ago)
    items = []
    for pn, qty in lines:
        p = sku_for(pn)
        items.append({"sku": p["sku"], "name": p["name"], "part_number": pn, "qty": qty, "unit_price": p["price_inc_vat"]})
    o = {
        "order_id": f"BSP{104200 + n}",
        "customer_name": name, "email": email, "delivery_area": area,
        "placed_at": placed.strftime("%Y-%m-%d %H:%M"),
        "status": status, "items": items,
        "total_inc_vat": round(sum(i["qty"] * i["unit_price"] for i in items), 2),
        "is_trade": "plumbing" in email or "bathrooms" in email,
    }
    if carrier:
        o["carrier"] = carrier
        o["tracking"] = f"{'15' if carrier == 'DPD' else 'RM'}{random.randint(10**9, 10**10 - 1)}{'GB' if carrier == 'Royal Mail' else ''}"
        o["dispatched_at"] = (placed + timedelta(days=1)).strftime("%Y-%m-%d")
    if status == "delivered":
        o["delivered_at"] = (placed + timedelta(days=2)).strftime("%Y-%m-%d")
    if status == "awaiting_stock":
        o["expected_stock_date"] = (today + timedelta(days=5)).strftime("%Y-%m-%d")
    orders.append(o)

# ---------------- Fictional customer emails (with ground truth) ----------------
E = []
def email(frm, subject, body, intent, expected=None, order=None, attachment=None, urgency="normal"):
    E.append({
        "id": f"EM{len(E)+1:03d}", "from": frm, "subject": subject, "body": body.strip(),
        "received_at": (today - timedelta(hours=len(E) * 2 + 1)).strftime("%Y-%m-%d %H:%M"),
        "attachment": attachment,
        "ground_truth": {"intent": intent, "expected_part_numbers": expected or [], "order_id": order, "urgency": urgency},
    })

email("sarah.whitfield@example.com", "Where is my order?",
      "Hi, I ordered a pink Aqualisa cartridge on Saturday, order BSP104201. Our shower is completely out so we really need it. When will it arrive? Thanks, Sarah",
      "order_status", order="BSP104201", urgency="high")
email("k.morgan@example.com", "Shower runs cold - which cartridge?",
      "Hello, our Aqualisa Midas 200 shower has started running lukewarm no matter where the temperature handle is set. We are on a mains pressure combi boiler. I think the cartridge has gone. Which one do I need?",
      "part_identification", expected=["518113"])
email("r.bello@example.com", "Flush button replacement",
      "Hi there, the flush plate on our wall hung toilet has cracked. It's white, rectangular, says Geberit on the back and has two rectangular buttons side by side. Photo attached. What do I need to order?",
      "part_identification", expected=["115.125.11.1", "115.135.11.1", "115.100.11.1"],
      attachment={"type": "image", "description": "White rectangular Geberit dual flush plate, two rectangular buttons side by side, crack across left button"})
email("gareth@hughes-plumbing.example.com", "Trade pricing for bulk Bathstore cartridges",
      "Morning. I'm refitting 12 flats that all have Bathstore Blade shower valves. Can you do trade pricing on 24 of the on/off cartridges SPACW0005 and 12 flow cartridges? Need them by end of next week. Gareth, Hughes Plumbing Ltd",
      "trade_quote", expected=["SPACW0005"])
email("priya.nair@example.com", "Wrong part?",
      "I received my Burlington diverter cartridge SP711 (order BSP104203) but it turns the wrong way - my valve closes anti-clockwise. Can I swap it? I haven't fitted it.",
      "return_exchange", expected=["SP711"], order="BSP104203")
email("t.fenwick@example.com", "Old Abacus valve cartridge",
      "We have an Abacus CVD concealed shower valve installed in about 2013. The thermostat isn't holding temperature. Which cartridge is the right one for a valve that old?",
      "part_identification", expected=["1099-0000-0004"])
email("j.patel@example.com", "Order BSP104206 still not shipped??",
      "I ordered a Cifial diverter cartridge 4 days ago and it still says not dispatched. This is ridiculous, I paid for next day delivery. I want an answer today or a refund.",
      "complaint", order="BSP104206", urgency="high")
email("h.osei@example.com", "Burlington Stour valve - top or bottom cartridge?",
      "I have a Burlington Stour BF3S thermostatic shower valve. The flow handle just spins and water won't turn off fully. The handle turns clockwise to close. Which cartridge do I need?",
      "part_identification", expected=["SP582"])
email("emma.clarke@example.com", "Flush plate scratched on arrival",
      "Hi, the chrome Geberit Delta 21 plate from order BSP104207 arrived with a deep scratch across the front. Photo attached. Please can you send a replacement?",
      "damaged_item", expected=["115.125.21.1"], order="BSP104207",
      attachment={"type": "image", "description": "Bright chrome Geberit Delta 21 flush plate with long scratch across face"})
email("n.green@example.com", "Do you deliver to Ireland?",
      "Hi, do you ship to the Republic of Ireland and how much would delivery be for a small cartridge?",
      "general_question")
email("c.daniels@example.com", "Bristan diverter",
      "Our Bristan Prism shower has a 4 function diverter that's leaking. The diverter is round, about 40mm. Is this the right shop?",
      "part_identification", expected=["D40P-19B2"])
email("linda.marsh@example.com", "Invoice request",
      "Could you send a VAT invoice for my order BSP104205 please? My landlord needs it.",
      "invoice_request", order="BSP104205")
email("s.yilmaz@example.com", "Aqualisa Opto cartridge - which colour?",
      "Our Aqualisa Opto concealed shower needs a new cartridge. We have a cold water tank in the loft and a hot water cylinder, no pump. The website shows pink, green and orange ones - which do I need?",
      "part_identification", expected=["265509", "022809"])
email("mike@brennanbathrooms.example.com", "Alpi parts - can you confirm dispatch",
      "Hi, just checking order BSP104208 for the Alpi cartridges will go out today, I've got a job booked Thursday. Cheers, Mike",
      "order_status", order="BSP104208", urgency="high")
email("a.kowalski@example.com", "Thermostatic cartridge from a photo",
      "I've taken the cartridge out of our shower. Photo attached. No brand name on the shower anywhere. Can you tell what it is?",
      "part_identification",
      attachment={"type": "image", "description": "Brass thermostatic cartridge, approx 120mm long, splined spindle, two O-rings, no visible markings"})
email("dlowe@example.com", "Wrong colour cartridge?",
      "Order BSP104210 - I ordered the orange Opto cartridge but now someone tells me I need the pink one because we have a combi boiler. Can I change it before it arrives?",
      "return_exchange", expected=["265502"], order="BSP104210")

# ---------------- Write ----------------
(OUT / "products.json").write_text(json.dumps(products, indent=2))
(OUT / "orders.json").write_text(json.dumps(orders, indent=2))
(OUT / "emails.json").write_text(json.dumps(E, indent=2))
(OUT / "data_quality.json").write_text(json.dumps(issues, indent=2))
print(f"products={len(products)} orders={len(orders)} emails={len(E)} issues={len(issues)}")
