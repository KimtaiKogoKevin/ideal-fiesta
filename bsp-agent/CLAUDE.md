# BSP Enquiry Agent – project context

## Why this exists
Kevin is applying for the **AI Automation & Development Specialist** role at **Bathroom Spare Parts** (Venezia Marketing, Hoddesdon, Herts; www.bathroomspareparts.co.uk). There's no CV stage. Applicants build a working AI prototype that solves a real problem for the business. The full ad is in `docs/job_ad.txt`.

**What they will judge:** creativity, functionality, technical ability, and potential value to the business. They want something that works, not a slide deck.

**What to submit:**
1. A link to a working prototype they can test, or a short screen recording
2. A brief explanation of the business problem it solves
3. How it was built (AI tools and technologies)
4. What Kevin would improve with access to their internal systems

**Hard rules from the ad:** public information, sample data or fictional customer records only. Never touch their internal systems or real customer data.

**Timeline:** no deadline given. Kevin wants it finished fast: core demo in about 2 days, polish on day 3.

## About Kevin
MSc AI & Robotics (Distinction). Strong Python, FastAPI, Django, Next.js, Docker. ML and perception background. Works part-time in a warehouse that uses MS Dynamics, Veeqo and Global-e. His laptop has no GPU, so everything goes through hosted APIs. Keep the stack simple and quick to ship.

## The business problem we chose
The ad says they handle **"hundreds of customer enquiries and orders every day"** across **thousands of products** from many brands. Most customers don't know which spare part they need: cartridges look alike and differ by rotation direction, water system, colour code, series and install date. So staff spend their time on "which part do I need?" and "where's my order?" emails.

## The solution: a customer enquiry agent
An agent built on Claude tool use (Anthropic API, plain tool use, no framework) that reads a customer email and:
- **classifies intent**: part_identification, order_status, trade_quote, return_exchange, damaged_item, complaint, invoice_request, general_question
- **identifies parts** from a description and/or photo (Claude vision), asking clarifying questions when unsure, and returning the top 3 matches with confidence when it can't be certain
- **looks up orders** in the fictional order data
- **drafts a reply** with product links, for a human to approve. It never sends anything or places orders on its own.
- flags urgency (shower out, trade job booked, angry customer)

**Tools:**
- `search_catalogue(query, brand?, category?, attributes?)`
- `get_product(sku)`
- `get_order(order_id or email)`
- `ask_customer(question)`, which gets added to the draft as a clarifying question
- `draft_reply(body, recommended_skus, confidence, intent, urgency, needs_human)`

**UI:** a simple inbox-style web page. A list of emails on the left. Click one to see the agent's **tool calls step by step** (this is the impressive part) and its draft reply, with Approve and Edit buttons. Add a "New enquiry" box where reviewers can type their own question and upload a real photo. It must work well on mobile.

**Eval:** `scripts/eval.py` runs the agent over `data/emails.json` and scores it against `ground_truth`: intent accuracy, correct part in the top 3, correct order ID. Put the score in the pitch.

**Stack:** FastAPI with the Anthropic Python SDK (model: latest Claude Sonnet, configured via an env var). Static HTML/JS or a light Next.js front end. Products, orders and emails load from JSON (SQLite is optional). The API key comes from the `ANTHROPIC_API_KEY` env var, falling back to `~/.config/bsp-agent/anthropic_key`; never printed, logged or hardcoded. Deploy to Render, Railway or Vercel. Keep a hard spend cap and rate limiting on the public demo.

## Data (already built: see `data/README.md`)
- `data/products.json`: 97 real public products (thermostatic, diverter and flow cartridges, Geberit flush plates) with parsed `attributes`
- `data/orders.json`: 10 fictional orders
- `data/emails.json`: 16 fictional emails with `ground_truth`
- `data/data_quality.json`: real catalogue issues (duplicates, ABSP-10-0018 listed at both £28 and £80, typos like "Flush Pate" and "Thermostaic"). Use these as a bonus slide.
- `scripts/build_data.py` regenerates all of the above
- `scripts/scrape_catalogue.py` is a polite local scraper (2 s delay) for a bigger catalogue with images. Optional.

## Plan / next steps
1. Backend: agent loop, tools, `/api/emails`, `/api/run/{email_id}` (stream tool-call steps), `/api/enquiry` (free text plus image upload)
2. Front end: inbox view with step-by-step trace and draft reply
3. `scripts/eval.py`, then iterate on the prompt and tools until part top-3 accuracy is high
4. Deploy and record a 60–90 s demo video
5. Write the submission: problem, how it was built (mention Claude Code), results, and next steps with internal access (live inbox via Gmail/Outlook API or an n8n workflow, EKM product and order API, full catalogue sync, supplier price-list ingestion agent, catalogue data-quality agent)

## Working preferences
Ship quickly and keep it simple. Prefer plain code over frameworks. Ask before big architectural changes.
