# Build log: BSP Enquiry Agent

A step-by-step record of what was built, why, and how it works, so you can explain every part in the interview.

---

## Step 0: Understand the brief and the data

**What:** Read `CLAUDE.md`, the job ad, and all four data files.

**What I found:**
- 97 real products in 4 categories (thermostatic, diverter and flow cartridges, flush plates) from 14 brands. Each has parsed `attributes`: `system` (combi / high pressure / gravity / low pressure), `rotation` (clockwise or anti-clockwise close), `colour_code`, `series`, `finish`, `size_mm`, `date_constraint`.
- 10 fictional orders in 4 states: processing, dispatched, delivered and awaiting_stock.
- 16 fictional emails, each with a `ground_truth` answer key: the correct intent, the acceptable part numbers, the order ID and the urgency.

**Why it matters:** the answer key is what turns "it seems to work" into "it scores X%". That number is the strongest evidence for the pitch.

---

## Step 1: Environment setup

**What:** Created a Python virtual environment (`.venv/`) and installed 4 libraries (`requirements.txt`):

| Library | Purpose |
|---|---|
| `anthropic` | Official Python SDK for calling Claude |
| `fastapi` | Web framework for the API |
| `uvicorn` | The server that runs FastAPI |
| `python-multipart` | Lets FastAPI accept photo uploads (form data) |

**Problem hit:** Ubuntu ships without the `venv` module, so you installed `python3.12-venv` with apt.

**API key:** `app/agent.py` (`_load_api_key()`) reads the `ANTHROPIC_API_KEY` environment variable first, and if it isn't set, falls back to the file `~/.config/bsp-agent/anthropic_key` (permissions 600). No `.env` file, no keyring, no `python-dotenv`. The key is only handed to the SDK client: it is never printed, logged or put in an error message. If no key is found, the agent returns a clean error event (naming the file to create, not the key) instead of crashing the stream.

---

## Step 2: The tools (`app/tools.py`)

**The core idea of tool use:** Claude can't read the JSON files itself. We describe each tool to Claude (name, purpose and input schema). When Claude wants data, it replies with a request like "call `search_catalogue` with these inputs". **Our Python code runs the function** and sends the result back. Claude decides *what* to look up; our code controls *how*, and nothing else is reachable.

The 5 tools:

| Tool | What the Python does |
|---|---|
| `search_catalogue(query, brand, category, attributes)` | Scores every product: +1 per query word found in the name, +10 for an exact part-number match, +2 per matching attribute. Returns the top 8. Brand and category are hard filters; attributes only boost the ranking, because many products have no parsed attributes. Part numbers are compared with punctuation removed, so "CR-101C" matches "CR101C". |
| `get_product(sku)` | Returns one product's full record, including the URL. It also accepts a manufacturer part number. |
| `get_order(order_id or email)` | Finds an order by ID (case-insensitive), or lists a customer's orders by email address. |
| `ask_customer(question)` | Stores the question so it can be shown with the draft. It sends nothing. |
| `draft_reply(...)` | The final step. Its *input* is the result: reply body, intent, recommended SKUs, order ID, confidence, urgency, `needs_human`, and a note for staff. |

**`strict: true`:** each tool definition sets this, which makes the API guarantee that Claude's inputs match the schema exactly (for example, intent is always one of the 8 allowed labels). The first live call failed because strict mode needs nullable fields with a fixed list of allowed values written as `anyOf: [{type: string, enum: [...]}, {type: null}]`. That one-line fix was the only bug.

**Why `draft_reply` is a tool rather than free text:** it forces a structured, machine-readable result that the UI can display and the eval can score automatically.

**Tested offline first:** before spending any API credit, I ran the search function on the test cases. The expected part ranked first or near the top in every case.

---

## Step 3: The agent loop (`app/agent.py`)

This is the "agent": a loop with no framework.

```
send email + tool list to Claude
repeat (max 10 times):
    Claude replies, either with text or with tool requests
    for each tool request: run the Python function, collect the result
    if Claude called draft_reply → done
    else send the results back to Claude and loop
```

Design choices:

- **It yields an event per step** (`yield {"type": "tool_call", ...}`). Both the web page (live trace) and the eval script consume the same stream, so what the reviewer sees is exactly what was scored.
- **System prompt:** written like training notes for a new staff member, covering how to tell lookalike parts apart (water system, rotation, install date, size, colour codes), what to do when unsure (top 3 candidates plus one clarifying question), shop policies (never invent trade prices; damage and refunds go to a human), and how to judge urgency. It contains no answers to specific test emails, only general rules.
- **Safety by design:** the agent only drafts. Every reply needs a person to approve it, and `needs_human` flags drafts where staff must act first (refund, swap, quote, invoice).
- **Model:** `claude-sonnet-5-5`, configurable with the `BSP_MODEL` environment variable. **Effort** is `medium`, a good balance of speed and quality for multi-step tool use (`BSP_EFFORT`).
- **Prompt caching:** the system prompt and tool definitions are the same on every call, so they're marked cacheable. Repeat calls read them at about 1/20 of the price; you can see this as `cache_read_input_tokens` in the usage numbers.
- **Refusal fallback:** if Anthropic's safety filter ever declines a request, the API retries on a fallback model in the same call (`fallbacks="default"`). It's unlikely for plumbing emails but costs nothing to keep.
- **"Today" is fixed** at 24 Nov 2026 so that answers about order dates match the fictional data.
- **Sample photos:** the sample emails carry a *text description* of their photo, which goes into the prompt. Real uploads from the web page are sent to Claude as actual images (vision).

---

## Step 4: The eval (`scripts/eval.py`)

**What:** runs the agent over all 16 emails (4 at a time in parallel) and compares each draft with `ground_truth`:

- **intent:** correct label?
- **part top-3:** is an acceptable part among the first 3 recommended? For the one email where the part is genuinely unidentifiable (EM015, no brand), success means the agent recommends nothing and asks questions instead.
- **order ID:** did it link the right order?
- **urgency:** was it right about high or normal?

It also reports cost and time, and saves full traces to `results/` (ignored by git).

**Run 1 (baseline):** intent 16/16, urgency 16/16, order 7/7, **part 9/11**. Reading the traces showed both misses were judgement calls:
- EM003 (cracked Geberit plate): about 20 similar white plates, so the agent asked good questions but suggested nothing. The brief says it should still give its top 3 when unsure.
- EM005 (wrong-way cartridge): the agent correctly found that no anti-clockwise version exists, but didn't list the item being returned, so staff wouldn't see that part on the draft.

**Fix:** two general prompt rules: (1) if you can narrow it to a brand and type, still give the 3 most likely candidates with low or medium confidence; (2) for returns and damage, list the customer's item plus any replacement.

**Runs 2 and 3:** **100% on every metric, both times.**

| Metric | Score |
|---|---|
| Intent | 16/16 |
| Correct part in top 3 | 11/11 |
| Correct order linked | 7/7 |
| Urgency | 16/16 |
| Cost | ~$0.013 per email (about 1p) |
| Speed | ~7 s per email |

**Be honest in the pitch:** 16 emails is a small test set, and I wrote the prompt while looking at it. Say so, and say the next step is a larger set of held-out emails. Interviewers respect that more than an unqualified "100%".

---

## Step 5: The web server (`app/main.py`)

| Endpoint | What it does |
|---|---|
| `GET /` | Serves the page |
| `GET /api/emails` | The sample inbox (answer keys stripped out) |
| `GET /api/run/{id}` | Runs the agent on a sample email and **streams** each step |
| `POST /api/enquiry` | Runs the agent on a typed message plus an optional photo (JPEG/PNG/WEBP/GIF, max 5 MB) |

**Streaming (Server-Sent Events):** instead of waiting about 7 s for one big response, the server sends each step as soon as it happens (`data: {...}\n\n`), so the trace builds up live on screen.

**Cost protection for a public demo:**
- A sample email's run is cached in memory after the first time. Repeat clicks replay it with short pauses, so it still looks live, for free. A "Re-run live" button forces a fresh run.
- Each visitor (IP address) gets 15 live runs per hour (`BSP_RUNS_PER_IP_PER_HOUR`).
- There's a global cap of 300 live runs per day (`BSP_DAILY_RUN_CAP`), which at about 1p per run limits the worst case to roughly £3 a day. **Also set a monthly spend limit in the Anthropic Console as a hard backstop.**

**Tested:** health check, inbox list, live run, cached replay (about 2 s), photo upload (the agent read a pink test image and picked the pink Opto cartridge), and bad file types (rejected).

---

## Step 6: The front end (`static/`)

Plain HTML, CSS and JavaScript, with no build step and no framework (`index.html`, `style.css`, `app.js`).

- **Left:** the inbox. **Right:** the email, then the **agent steps** timeline, then the **draft reply**.
- Each step shows a plain-English summary (e.g. *Searched the catalogue: "Midas 200" · 14 matches, top: 518113*), with a "Raw tool call" toggle that shows the exact JSON. That's the technical evidence for reviewers.
- The draft card shows tags (intent, urgency, confidence, "needs staff action"), the note for staff, product cards with price and a link, and the editable reply with **Approve** and **Edit** buttons. Approving only marks the draft as approved; the demo never sends anything.
- The footer of each draft shows the number of model calls, the time and the cost of that run.
- **New enquiry** opens a form for typing a question and uploading a photo.
- **Mobile:** under 760 px wide the page shows one column, the inbox opens the email full-screen, and a "← Inbox" button goes back. Dark mode follows the phone or computer setting.
- All text from emails and the model is HTML-escaped before display, so injected HTML can't run.

---

## Step 7: Hardening for the demo (robustness pass)

A review before demoing found five weak spots. Each fix, and how it was tested:

**1. Clicking another email mid-run broke the page** (`static/app.js`). The old stream kept writing into the new email's trace, and could even put its draft under the wrong email. Now every run has its own `AbortController`: opening another email cancels the old request, and any late events from a cancelled run are ignored.

**2. Phone photos were often rejected.** Phone photos are often over 5 MB, and the server trusted the browser's file-type label. Now:
- the browser shrinks the photo before upload (longest side 1568 px, the size Claude's vision works best at, saved as JPEG);
- the server checks the file's first bytes (its "magic number") to decide whether it's really a JPEG, PNG, WEBP or GIF;
- an unreadable file (e.g. HEIC on a desktop browser) gets a helpful message.

*Tested:* a text file renamed `.jpg` and a 6 MB file are both rejected, and neither uses up a live run.

**3. Order privacy, enforced in code** (`tools.py`, `get_order`). Before, anyone could type "what's the status of order BSP104201?" into New enquiry and see another customer's order. Now `get_order` only returns orders that belong to the email's sender. The sender comes from the email itself, never from the model, so no wording of the message can get around it. *Tested live:* "nosy@example.com" asked for Sarah's order. The tool refused, the reply politely declined, and no tracking number or carrier leaked. **Interview point:** security rules belong in code, not just in the prompt.

**4. Fact-checking the draft** (`tools.py`, `verify_draft`). After `draft_reply`, the code checks the draft:
- every recommended SKU exists and was actually looked up;
- every £ price in the reply matches a product or order the agent saw;
- every link is a real product page;
- the order ID was successfully looked up.

Any problem sets `needs_human`, adds a "⚠ check facts" tag, and shows as a step in the trace. *Tested:* there were no false alarms on all 16 eval drafts, a deliberately fake draft was caught on all 4 checks, and in the privacy test above it caught the agent putting the blocked order ID on the draft.

**5. Persistence with SQLite** (`app/db.py`, standard-library `sqlite3`, no ORM). Before, everything lived in memory, so a page reload lost approvals and a restart lost the cached runs and reset the spend cap. Now one SQLite file stores:

| Table | What it keeps |
|---|---|
| `runs` | every finished agent run (the full step-by-step event list) |
| `enquiries` | reviewers' own enquiries, including the photo |
| `drafts` | edited / approved status and the edited text |
| `live_runs` | timestamps for the per-IP and daily limits |

- **Visitors** are told apart by a random cookie, with no login. Each visitor sees only their own enquiries, photos and approvals. Sample-email runs are shared, which is what makes repeat views free.
- **Seed runs:** `scripts/eval.py --seed` saves the scored runs to `data/seed_runs.json`. On startup the app loads them into SQLite, so a fresh deploy replays the whole inbox instantly before anyone has paid for a live run.
- *Tested:* approve, restart the server, and the approval and edited text are still there. A second visitor doesn't see them, and can't open the first visitor's enquiry or photo (404).

**Eval after the changes:** still 100% on all four metrics (16/16 intent, 16/16 urgency, 7/7 order, 11/11 part top-3), ~$0.014 and ~7 s per email.

**Deploying with the disk:** `render.yaml` (at the repo root, with `rootDir: bsp-agent`) is a Render blueprint: a Starter web service with a 1 GB disk mounted at `/var/data`, and `BSP_DB_PATH=/var/data/app.db`. Set `ANTHROPIC_API_KEY` in the dashboard.

---

## How to run it

```bash
cd bsp-agent
.venv/bin/uvicorn app.main:app --port 8000      # open http://localhost:8000
.venv/bin/python scripts/eval.py                # score all 16 emails (~$0.21)
.venv/bin/python scripts/eval.py EM003 EM005    # score just some
.venv/bin/python scripts/eval.py --seed         # score all + save demo seed runs
# BSP_DB_PATH=/path/app.db sets where SQLite lives (default data/app.db)
```

## File map

```
bsp-agent/
  app/tools.py      the 5 tools + their schemas
  app/agent.py      system prompt + agent loop
  app/main.py       FastAPI server, streaming, rate limits, visitor cookie
  app/db.py         SQLite: runs, enquiries, draft status, rate limits
  static/           index.html, style.css, app.js
  scripts/eval.py   scoring against ground_truth
  data/             products, orders, emails, seed_runs.json (app.db is git-ignored)
  render.yaml       Render blueprint with persistent disk
  requirements.txt
```

## Still to do (from the plan)

1. **Deploy** on Render from `render.yaml` (Starter plan + 1 GB disk); set `ANTHROPIC_API_KEY` in the dashboard. Also set a monthly spend limit in the Anthropic Console.
2. **Record a 60–90 s demo:** a part-ID email, an angry complaint, and a live photo upload.
3. **Write the submission** (problem, how it was built, results table above, next steps with internal access).
4. Optional: scrape a bigger catalogue with images, and add more held-out test emails.
