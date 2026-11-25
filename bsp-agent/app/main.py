"""FastAPI web server: serves the inbox page and streams agent runs to it.

Endpoints
  GET  /                         the single-page UI (static/index.html)
  GET  /api/emails               sample inbox (ground truth stripped) + this visitor's own enquiries
  GET  /api/run/{email_id}       run the agent on an email, streamed as Server-Sent Events
                                 (replays the saved run unless ?fresh=1)
  POST /api/enquiry              save a typed enquiry + optional photo, then run the agent on it (SSE)
  GET  /api/enquiry/{id}/image   the photo of one of this visitor's enquiries
  POST /api/drafts/{run_id}      save a draft as edited/approved (with the edited text)

Everything is stored in SQLite (app/db.py), so runs, enquiries, approvals and
rate limits survive page reloads and server restarts.

Visitors are told apart by a random cookie (no login). Each visitor sees only
their own enquiries and their own approvals; agent runs of the sample emails
are shared, which is what makes repeat views free.

Cost protection: saved runs replay for free, each IP gets a limited number of
live runs per hour, and there is a global daily cap on live runs.
"""
import json
import os
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .agent import MODEL, run_agent

ROOT = Path(__file__).resolve().parent.parent
EMAILS = json.loads((ROOT / "data" / "emails.json").read_text())
EMAILS_BY_ID = {e["id"]: e for e in EMAILS}

PER_IP_PER_HOUR = int(os.getenv("BSP_RUNS_PER_IP_PER_HOUR", "15"))
DAILY_CAP = int(os.getenv("BSP_DAILY_RUN_CAP", "300"))
MAX_IMAGE_BYTES = 5 * 1024 * 1024
VISITOR_COOKIE = "bsp_vid"
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

app = FastAPI(title="BSP Enquiry Agent")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
db.init(set(EMAILS_BY_ID))


@app.middleware("http")
async def visitor_cookie(request: Request, call_next):
    """Give each browser a random ID so its enquiries and approvals are its own."""
    vid = request.cookies.get(VISITOR_COOKIE, "")
    is_new = not VISITOR_RE.match(vid)
    if is_new:
        vid = secrets.token_urlsafe(18)
    request.state.visitor = vid
    response = await call_next(request)
    if is_new:
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(VISITOR_COOKIE, vid, max_age=365 * 86400, httponly=True, samesite="lax", secure=https)
    return response


def _check_limits(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    error = db.take_live_run(ip, PER_IP_PER_HOUR, DAILY_CAP)
    if error:
        raise HTTPException(429, error)


def _sniff_image(data: bytes) -> str | None:
    """Decide the image type from the file's first bytes, not from what the browser claims."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sse_event(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


def _live(email_id: str, events, head: list[dict] = ()):
    """Stream a live run, then save it if it produced a draft."""
    for ev in head:
        yield _sse_event(ev)
    collected = []
    for ev in events:
        collected.append(ev)
        yield _sse_event(ev)
    if any(e["type"] == "final" for e in collected):
        run_id = db.save_run(email_id, collected)
        yield _sse_event({"type": "run", "run_id": run_id, "cached": False, "draft_state": None})
    yield _sse_event({"type": "done"})


def _replay(run_id: int, events: list[dict], visitor: str):
    """Replay a saved run with a short pause per step, so it still reads as a live trace."""
    yield _sse_event({"type": "run", "run_id": run_id, "cached": True,
                      "draft_state": db.get_draft(visitor, run_id)})
    for ev in events:
        time.sleep(0.35)
        yield _sse_event(ev)
    yield _sse_event({"type": "done"})


def _stream(gen):
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/emails")
def list_emails(request: Request):
    visitor = request.state.visitor
    latest = db.latest_run_ids()
    statuses = db.draft_statuses(visitor)

    def extra(eid: str) -> dict:
        run_id = latest.get(eid)
        return {"cached": run_id is not None, "status": statuses.get(run_id) if run_id else None}

    samples = [{k: v for k, v in e.items() if k != "ground_truth"} | extra(e["id"]) for e in EMAILS]
    mine = [e | extra(e["id"]) for e in db.list_enquiries(visitor)]
    return {"model": MODEL, "emails": mine + samples}


def _find_email(email_id: str, visitor: str) -> tuple[dict, tuple[bytes, str] | None]:
    if email_id in EMAILS_BY_ID:
        return EMAILS_BY_ID[email_id], None
    found = db.get_enquiry(visitor, email_id)
    if not found:
        raise HTTPException(404, "Unknown email")
    return found


@app.get("/api/run/{email_id}")
def run_email(email_id: str, request: Request, fresh: bool = False):
    email, image = _find_email(email_id, request.state.visitor)
    if not fresh:
        saved = db.latest_run(email_id)
        if saved:
            return _stream(_replay(saved[0], saved[1], request.state.visitor))
    _check_limits(request)
    return _stream(_live(email_id, run_agent(email, image=image)))


@app.post("/api/enquiry")
async def enquiry(request: Request, body: str = Form(""), sender: str = Form(""),
                  subject: str = Form(""), image: UploadFile | None = File(None)):
    if not body.strip() and not (image and image.filename):
        raise HTTPException(400, "Type a question or attach a photo.")
    if len(body) > 4000:
        raise HTTPException(400, "Please keep the enquiry under 4000 characters.")
    img = None
    if image and image.filename:
        data = await image.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "Photo must be under 5 MB.")
        media_type = _sniff_image(data)
        if not media_type:
            raise HTTPException(400, "Photo must be a JPEG, PNG, WEBP or GIF (iPhone HEIC photos: "
                                     "change Camera > Formats to 'Most Compatible', or take a screenshot).")
        img = (data, media_type)
    _check_limits(request)
    email = {"from": sender.strip()[:200] or "visitor@example.com",
             "subject": subject.strip()[:200] or "Website enquiry",
             "received_at": time.strftime("%Y-%m-%d %H:%M"),
             "body": body.strip() or "(see attached photo)"}
    eid = db.add_enquiry(request.state.visitor, email, img)
    head = [{"type": "enquiry", "email": email | {"id": eid, "has_image": img is not None}}]
    return _stream(_live(eid, run_agent(email, image=img), head=head))


@app.get("/api/enquiry/{eid}/image")
def enquiry_image(eid: str, request: Request):
    found = db.get_enquiry(request.state.visitor, eid)
    if not found or not found[1]:
        raise HTTPException(404, "No photo")
    data, media_type = found[1]
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})


class DraftUpdate(BaseModel):
    status: str = Field(pattern="^(edited|approved)$")
    body: str = Field(max_length=10000)


@app.post("/api/drafts/{run_id}")
def save_draft(run_id: int, update: DraftUpdate, request: Request):
    if not db.run_exists(run_id):
        raise HTTPException(404, "Unknown run")
    db.set_draft(request.state.visitor, run_id, update.status, update.body)
    return {"ok": True}


@app.get("/healthz")
def health():
    return {"ok": True}
