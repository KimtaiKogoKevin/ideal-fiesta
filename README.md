# BSP Enquiry Agent

An AI agent that reads customer emails for [Bathroom Spare Parts](https://www.bathroomspareparts.co.uk), works out which spare part the customer needs or where their order is, and drafts a reply for a member of staff to approve. It never sends anything on its own.

**Live demo:** _[link — add after deploy]_

Built for the Bathroom Spare Parts AI Automation & Development Specialist challenge. Products and prices are real public data from the website; all customers, orders and emails are fictional.

---

## Contents

- [The problem](#the-problem)
- [How it works](#how-it-works)
- [Codebase tour](#codebase-tour)
- [Run it locally](#run-it-locally)
- [Configuration](#configuration)
- [Evaluation](#evaluation)
- [Safety and cost controls](#safety-and-cost-controls)
- [Deploying](#deploying)

---

## The problem

The shop handles hundreds of enquiries a day across thousands of parts. Most of them come down to two questions: **"which part do I need?"** and **"where's my order?"**

The first is hard because many cartridges look alike but aren't interchangeable. They differ by:

- water system (combi or gravity; Aqualisa even colour-codes them)
- rotation direction
- install date
- size and series

Customers rarely know these details, so an experienced member of staff has to work them out for each email.

## How it works

```
 customer email ──► Claude agent ──► draft reply ──► fact check ──► staff approve / edit
 (text or photo)        │  ▲          (+ note for      (code, not AI)   (nothing is sent
                   calls │  │ results     staff)                        automatically)
                        ▼  │
                 ┌──────────────────────────┐
                 │ search_catalogue         │
                 │ get_product              │
                 │ get_order (sender only)  │
                 │ ask_customer             │
                 └──────────────────────────┘
```

1. **The email goes to Claude** (Sonnet 5.5) with a description of five tools.
2. **Claude decides what to look up.** For example, it might search the catalogue for "Aqualisa Midas 200" with `system: combi`. Our Python code runs the search and sends back the results. This repeats, up to 10 steps, until Claude knows enough.
3. **Claude finishes by calling `draft_reply`.** It must fill a strict schema: intent, urgency, confidence, recommended SKUs, order ID, the reply text and a note for staff. This makes the output machine-readable for the UI and the eval.
4. **Code fact-checks the draft.** Every SKU, £ price, link and order ID in the reply must match data the agent actually looked up. Any mismatch flags the draft for staff.
5. **A person reviews it** in the inbox UI and approves or edits it.

Every step is streamed to the browser as it happens, so you can watch the agent work.

No agent framework is used. The loop in `app/agent.py` is about 100 lines of plain Python around the Anthropic SDK.

## Codebase tour

```
render.yaml              Render deployment blueprint (web service + persistent disk)
bsp-agent/
├── app/
│   ├── agent.py         The agent: system prompt, API key loading, the tool-use loop
│   ├── tools.py         The 5 tools, their JSON schemas, and the draft fact-checker
│   ├── main.py          FastAPI server: endpoints, streaming, rate limits, visitor cookie
│   └── db.py            SQLite storage: runs, enquiries, draft approvals, rate limits
├── static/
│   ├── index.html       Single-page inbox UI
│   ├── app.js           Streams a run, renders each step and the draft, approve/edit
│   └── style.css        Layout (works on mobile), light and dark mode
├── scripts/
│   ├── eval.py          Scores the agent against the labelled test emails
│   ├── build_data.py    Regenerates the data files
│   ├── raw_products.py  Product listings collected from the public website
│   └── scrape_catalogue.py  Optional polite scraper for a bigger catalogue
├── data/
│   ├── products.json    97 real products from 14 brands, with parsed attributes
│   ├── orders.json      10 fictional orders
│   ├── emails.json      16 fictional emails, each with a ground_truth answer key
│   ├── seed_runs.json   Pre-recorded agent runs so the demo inbox loads instantly
│   └── data_quality.json  Real issues found in the public catalogue
└── docs/
    └── BUILD_LOG.md     Step-by-step record of how and why everything was built
```

### `app/agent.py`: the agent loop

`run_agent(email, image=None)` is a **generator** that yields one event per step:

| Event type | Meaning |
|---|---|
| `tool_call` | A tool was run: its name, input and output |
| `text` | A note the model wrote between steps |
| `check` | The fact-check result |
| `usage` | Tokens, API calls, time taken |
| `final` | The finished draft |
| `error` | Something went wrong; the run stops |

The web server and the eval both consume this same stream, so what you see in the UI is exactly what was scored.

Other details:

- **Prompt caching:** the system prompt and tools are marked cacheable, so repeat calls are cheaper.
- **Vision:** uploaded photos are sent as real images, so Claude can read them.
- **API key:** read from `ANTHROPIC_API_KEY`, falling back to `~/.config/bsp-agent/anthropic_key`. It is never logged.

### `app/tools.py`: tools and guardrails

| Function | What it does |
|---|---|
| `search_catalogue` | Keyword scoring over products. Part numbers are matched without punctuation (CR-101C = CR101C). Brand and category are hard filters; attributes like `system` or `rotation` boost the ranking. |
| `get_product` | One product by SKU or manufacturer part number |
| `get_order` | An order by ID or by email address, **but only if it belongs to the sender**. The sender comes from the email, not from the model, so no wording in an email can reveal another customer's order. |
| `ask_customer` | Records a clarifying question for the reply |
| `verify_draft` | Runs after the draft: checks every SKU, price, link and order ID against what the tools returned |

`TOOLS` holds the JSON schemas Claude sees. They use `strict: true`, so Claude's inputs always match the schema.

### `app/main.py`: web server

| Endpoint | Purpose |
|---|---|
| `GET /api/emails` | Sample inbox (answer keys stripped) plus this visitor's own enquiries |
| `GET /api/run/{id}` | Streams a run as Server-Sent Events. Replays the saved run unless `?fresh=1`. |
| `POST /api/enquiry` | A typed enquiry plus optional photo. The photo type is checked from the file's bytes; max 5 MB. |
| `GET /api/enquiry/{id}/image` | The photo of one of your enquiries |
| `POST /api/drafts/{run_id}` | Saves a draft as `edited` or `approved` |
| `GET /healthz` | Health check |

Each browser gets a random cookie (no login). Visitors see only their own enquiries and approvals.

### `app/db.py`: storage

SQLite via Python's built-in `sqlite3`, with no ORM.

| Table | Holds |
|---|---|
| `runs` | Every finished agent run, as its full event list |
| `enquiries` | Visitors' own enquiries, including photos |
| `drafts` | Edited or approved status and the edited text |
| `live_runs` | Timestamps for the per-IP and daily limits |

On startup the app loads `data/seed_runs.json` for any sample email without a saved run.

### `static/`: front end

Plain HTML, CSS and JavaScript, with no build step:

- Each run has its own `AbortController`, so switching emails mid-run cancels the old stream cleanly.
- Large phone photos are resized in the browser before upload.
- All text from emails and from the model is HTML-escaped before display.

## Run it locally

Requires Python 3.12 and an Anthropic API key.

```bash
cd bsp-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# API key: either an environment variable...
export ANTHROPIC_API_KEY=...
# ...or a file (keep it private)
mkdir -p ~/.config/bsp-agent && chmod 700 ~/.config/bsp-agent
# then save the key into ~/.config/bsp-agent/anthropic_key and: chmod 600 ~/.config/bsp-agent/anthropic_key

.venv/bin/uvicorn app.main:app --port 8000
# open http://localhost:8000
```

The sample inbox replays the pre-recorded runs without calling the API. "Re-run live" and "New enquiry" make real calls.

## Configuration

All settings are environment variables with sensible defaults:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | API key; falls back to `~/.config/bsp-agent/anthropic_key` |
| `BSP_MODEL` | `claude-sonnet-5-5` | Claude model |
| `BSP_EFFORT` | `medium` | Reasoning effort |
| `BSP_MAX_STEPS` | `10` | Maximum agent loop iterations |
| `BSP_TODAY` | `Tuesday 24 November 2026` | Pinned "today" so answers match the fictional order dates |
| `BSP_RUNS_PER_IP_PER_HOUR` | `15` | Live runs allowed per visitor IP per hour |
| `BSP_DAILY_RUN_CAP` | `300` | Live runs allowed per day in total |
| `BSP_DB_PATH` | `data/app.db` | SQLite file location |

## Evaluation

```bash
.venv/bin/python scripts/eval.py               # all 16 emails (~$0.22)
.venv/bin/python scripts/eval.py EM003 EM005   # just some
.venv/bin/python scripts/eval.py --seed        # all, and refresh data/seed_runs.json
```

Each test email has a `ground_truth` answer key. Latest results:

| Measure | Score |
|---|---|
| Intent (what the customer wants) | 16 / 16 |
| Correct part in the top 3 | 11 / 11 |
| Correct order linked | 7 / 7 |
| Urgency | 16 / 16 |
| Fact-check passes | 16 / 16 |
| Cost / time per email | ~1.4¢ / ~7 s |

**Caveat:** 16 emails is a small set, and the prompt was developed while looking at them. A larger held-out set, ideally real anonymised enquiries, is the next step.

## Safety and cost controls

- **Human in the loop:** the agent only drafts; a person approves every reply.
- **Order privacy enforced in code:** `get_order` only returns the sender's own orders.
- **Fact-checked drafts:** made-up SKUs, prices, links or order IDs are flagged.
- **Policy rules in the prompt:** it never invents trade prices, refunds or delivery costs; those go to staff.
- **Spend limits:** saved runs replay for free, plus the per-IP hourly limit and the daily cap. Also set a monthly spend limit in the Anthropic Console.
- **Uploads:** photos are checked by their actual bytes (not the browser's label), capped at 5 MB, and only visible to the visitor who sent them.

## Deploying

`render.yaml` deploys to [Render](https://render.com) as a Blueprint:

- a Starter web service with `rootDir: bsp-agent`
- a 1 GB persistent disk at `/var/data` for the SQLite file

Set `ANTHROPIC_API_KEY` in the Render dashboard when prompted. Never commit it.

## Built with

Claude Sonnet 5.5 (Anthropic API, tool use and vision), Python 3.12, FastAPI, SQLite and vanilla JavaScript. Developed with [Claude Code](https://claude.com/claude-code).
