"""Polite scraper for the PUBLIC bathroomspareparts.co.uk catalogue.

Run locally:  pip install requests beautifulsoup4
              python scrape_catalogue.py --limit 300 --out ../data/products_full.json

- Only public category/product pages, 2s delay between requests, identifies itself.
- Pulls name, price, part number, main image and description for each product.
"""
import argparse, json, re, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://www.bathroomspareparts.co.uk/"
CATEGORIES = {
    "thermostatic_cartridge": "shower-thermostatic-cartridges-2029-c.asp",
    "diverter_cartridge": "shower-diverter-cartridges-2032-c.asp",
    "flow_cartridge": "shower-flow-valves-and-shut-off-cartridges-2031-c.asp",
    "flush_plate": "geberit-flush-plates-1667-c.asp",
}
HEADERS = {"User-Agent": "BSP-AI-Challenge-Prototype/0.1 (job application demo; low rate)"}
S = requests.Session(); S.headers.update(HEADERS)

def get(url):
    time.sleep(2)
    r = S.get(url, timeout=30); r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")

def product_links(cat_path):
    soup = get(urljoin(BASE, cat_path))
    links = {urljoin(BASE, a["href"]) for a in soup.select("a[href]") if a["href"].endswith("-p.asp")}
    return sorted(links)

def product(url, category):
    soup = get(url)
    name = (soup.find("h1") or soup.find("title")).get_text(" ", strip=True)
    text = soup.get_text(" ", strip=True)
    price = re.search(r"£\s?([\d,]+\.\d{2})", text)
    img = soup.find("meta", property="og:image")
    desc = soup.find("meta", attrs={"name": "description"})
    pn = re.search(r"([A-Z0-9][A-Z0-9.\-/]{3,})\s*$", name)
    return {"name": name, "category": category, "url": url,
            "price_inc_vat": float(price.group(1).replace(",", "")) if price else None,
            "part_number": pn.group(1) if pn else None,
            "image_url": img["content"] if img else None,
            "description": desc["content"] if desc else None}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--out", default="../data/products_full.json"); a = ap.parse_args()
    out = []
    for cat, path in CATEGORIES.items():
        for url in product_links(path):
            if len(out) >= a.limit: break
            try: out.append(product(url, cat)); print(len(out), out[-1]["name"])
            except Exception as e: print("skip", url, e)
    json.dump(out, open(a.out, "w"), indent=2)
