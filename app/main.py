"""FastAPI app: intake, tracking, and a small dashboard."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import db
from .extract import clarification_for, extract
from .models import Extracted, RequestIn, RequestOut, Status, StatusUpdate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ops.api")

# Which status changes are allowed. Anything else is a 409.
TRANSITIONS: dict[Status, set[Status]] = {
    Status.NEEDS_INFO: {Status.NEW, Status.REJECTED},
    Status.NEW: {Status.APPROVED, Status.REJECTED, Status.NEEDS_INFO},
    Status.APPROVED: {Status.ORDERED, Status.REJECTED},
    Status.ORDERED: {Status.DONE},
    Status.DONE: set(),
    Status.REJECTED: set(),
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="Purchase Request Tracker", lifespan=lifespan)


def _to_out(row) -> RequestOut:
    extracted = Extracted.model_validate(json.loads(row["extracted"]))
    missing = extracted.missing_fields()
    return RequestOut(
        id=row["id"], text=row["text"], extracted=extracted, missing=missing,
        clarification=clarification_for(missing) if row["status"] == Status.NEEDS_INFO else None,
        method=row["method"], status=row["status"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


@app.post("/requests", response_model=RequestOut, status_code=201)
def create_request(body: RequestIn):
    fields, method = extract(body.text)
    if body.department:  # an explicit form field beats anything inferred from text
        fields.department = body.department
    status = Status.NEEDS_INFO if fields.missing_fields() else Status.NEW
    with db.connect() as conn:
        rid = db.insert(conn, body.text, fields.model_dump(mode="json"), method, status)
        row = db.get(conn, rid)
    log.info("request %s created via %s, status=%s, missing=%s", rid, method, status.value, fields.missing_fields())
    return _to_out(row)


@app.get("/requests", response_model=list[RequestOut])
def list_requests(status: Status | None = Query(default=None)):
    with db.connect() as conn:
        return [_to_out(r) for r in db.list_all(conn, status.value if status else None)]


@app.get("/requests/{request_id}", response_model=RequestOut)
def get_request(request_id: int):
    with db.connect() as conn:
        row = db.get(conn, request_id)
    if not row:
        raise HTTPException(404, f"request {request_id} not found")
    return _to_out(row)


@app.patch("/requests/{request_id}/fields", response_model=RequestOut)
def update_fields(request_id: int, body: Extracted):
    """Fill in answers to the clarification. Unset fields are left alone."""
    with db.connect() as conn:
        row = db.get(conn, request_id)
        if not row:
            raise HTTPException(404, f"request {request_id} not found")
        current = Extracted.model_validate(json.loads(row["extracted"]))
        merged = current.model_copy(update=body.model_dump(exclude_unset=True))
        conn.execute("UPDATE requests SET extracted = ? WHERE id = ?",
                     (json.dumps(merged.model_dump(mode="json")), request_id))
        if row["status"] == Status.NEEDS_INFO and not merged.missing_fields():
            db.set_status(conn, request_id, row["status"], Status.NEW.value)
            log.info("request %s complete, moved to new", request_id)
        row = db.get(conn, request_id)
    return _to_out(row)


@app.patch("/requests/{request_id}/status", response_model=RequestOut)
def update_status(request_id: int, body: StatusUpdate):
    with db.connect() as conn:
        row = db.get(conn, request_id)
        if not row:
            raise HTTPException(404, f"request {request_id} not found")
        old = Status(row["status"])
        if body.status not in TRANSITIONS[old]:
            raise HTTPException(409, f"cannot move from {old.value} to {body.status.value}")
        if body.status == Status.NEW and Extracted.model_validate(json.loads(row["extracted"])).missing_fields():
            raise HTTPException(409, "request still has missing fields; fill them in first")
        db.set_status(conn, request_id, old.value, body.status.value)
        row = db.get(conn, request_id)
    log.info("request %s: %s -> %s", request_id, old.value, body.status.value)
    return _to_out(row)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
