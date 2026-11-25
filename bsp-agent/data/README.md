# BSP Enquiry Agent – demo data

| File | What it is | Real or fictional |
|---|---|---|
| `products.json` | 97 products (thermostatic, diverter and flow cartridges, Geberit flush plates) with brand, part number, price, URL and parsed attributes (rotation, system type, colour code, series, finish, date constraints) | **Real**, from public category pages, 24 Nov 2026 |
| `orders.json` | 10 orders with statuses: processing, dispatched (with tracking), delivered, awaiting stock | **Fictional**, using `example.com` customers |
| `emails.json` | 16 customer emails covering order status, part ID (some with photo descriptions), trade quotes, returns, damage, complaints, invoices and general questions. Each has a `ground_truth` label for scoring the agent | **Fictional** |
| `data_quality.json` | Issues found in the public catalogue: duplicate listings, one part number at two prices (ABSP-10-0018: £28 vs £80), title typos | **Real** findings |

Rebuild with `python scripts/build_data.py`. Pull a larger catalogue with images by running `scripts/scrape_catalogue.py` on your own machine.

The `ground_truth` labels let you report accuracy, e.g. "correct intent on 15/16 emails, correct part in the top 3 on 7/8".
