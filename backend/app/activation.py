import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .auth import identity, require_role
from .domain import Case
from .store import PostgresStore
from .verification import GST_NAME_CHECK_VERSION, check_document, corrected_bill, extract, norm, specimen_image, specimens

router = APIRouter(prefix="/api/activation")
Document = Literal["address", "signatory", "gst", "udyam"]
DOC_FILES = {"address": "address_proof", "signatory": "signatory_id", "gst": "gst_certificate", "udyam": "udyam_certificate"}
LIMIT = 5_000_000
NOTICE = "saathi-synthetic-v1"


def now():
    return datetime.now(timezone.utc)


def store_for(request: Request):
    store = request.app.state.store
    if not isinstance(store, PostgresStore):
        raise HTTPException(503, detail="activation_requires_postgres")
    return store


def transaction(store: PostgresStore, key: str, operation):
    with store._connect() as conn:
        row = conn.execute("SELECT payload FROM owner_cases WHERE owner_key = %s FOR UPDATE", (key,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(409, detail="quote_required")
        case = Case.model_validate(row[0])
        if not case.quote or not case.accepted_at:
            raise HTTPException(409, detail="quote_required")
        saved = conn.execute(
            "SELECT payload FROM activation_cases WHERE owner_key = %s AND quote_id = %s",
            (key, case.quote.id),
        ).fetchone()
        state = saved[0] if saved else {"docs": {}, "pending": {}}
        gst = state["docs"].get("gst")
        if (not state.get("order") and gst and gst["status"] == "pass"
                and gst.get("check_version") != GST_NAME_CHECK_VERSION):
            state["docs"]["gst"] = {**gst, "status": "action", "reason": "recapture"}
        result = operation(conn, case, state)
        conn.execute(
            "INSERT INTO activation_cases (owner_key, quote_id, payload) VALUES (%s,%s,%s) "
            "ON CONFLICT(owner_key) DO UPDATE SET quote_id=excluded.quote_id,payload=excluded.payload,updated_at=now()",
            (key, case.quote.id, Jsonb(state)),
        )
        return result


def ensure_consent(state):
    if not state.get("consent"):
        raise HTTPException(409, detail="consent_required")


def directory(request):
    return Path(os.getenv("SAATHI_DATA_DIR") or request.app.state.data_directory)


def ready(case, state, catalogue):
    docs = state["docs"]
    required = {"address", "signatory"}
    if any(catalogue.products[line.sku].needs_gst for line in case.quote.lines if line.sku in catalogue.products):
        required.add("gst")
    return bool(state.get("consent") and required <= docs.keys()
                and not state["pending"] and all(doc["status"] == "pass" for doc in docs.values()))


def public(case, state, catalogue):
    return {
        "consented": bool(state.get("consent")),
        "business": state.get("business", ""),
        "docs": state["docs"],
        "pending": list(state["pending"]),
        "ready": ready(case, state, catalogue),
        "handoff": state.get("handoff"),
        "order": state.get("order"),
    }


@router.get("")
def read(request: Request):
    key = require_role(request, "owner")
    return transaction(store_for(request), key, lambda _, case, state: public(case, state, request.app.state.catalogue))


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    accepted: bool
    notice: Literal["saathi-synthetic-v1"]
    language: Literal["en", "hi", "mr"]
    business: str = Field(default="", max_length=120)


@router.post("/consent")
def consent(request: Request, choice: Consent):
    key = require_role(request, "owner")

    def apply(_, case, state):
        if state.get("order") and choice.accepted:
            raise HTTPException(409, detail="order_already_placed")
        decision = {"version": NOTICE, "language": choice.language, "at": now().isoformat(), "accepted": choice.accepted}
        if choice.accepted:
            if len(norm(choice.business)) < 3:
                raise HTTPException(422, detail="business_required")
            if state.get("business") != choice.business:
                state.update(docs={}, pending={}, handoff=None)
            state.update(consent={**decision, "receipt": str(uuid4())}, business=choice.business)
        else:
            state.update(consent=None, business="", docs={}, pending={}, handoff=None)
        state["last_consent_decision"] = decision
        return public(case, state, request.app.state.catalogue)

    return transaction(store_for(request), key, apply)


def process_document(request, key, kind, data, expected_quote_id, replacement=False):
    store = store_for(request)
    data_directory = directory(request)
    file_path = specimens(data_directory).get(hashlib.sha256(data).hexdigest())
    if not replacement and (not file_path or file_path.stem.split("_", 1)[1] != DOC_FILES[kind]):
        raise HTTPException(422, detail="supplied_specimens_only")
    try:
        image = specimen_image(data, kind=kind)
    except ValueError as error:
        raise HTTPException(422, detail="invalid_specimen") from error
    job = str(uuid4())

    def start(_, case, state):
        ensure_consent(state)
        if state.get("order") or case.quote.id != expected_quote_id:
            raise HTTPException(409, detail="quote_changed")
        if kind in state["pending"]:
            started = datetime.fromisoformat(state["pending"][kind]["at"])
            if now() - started < timedelta(minutes=3):
                raise HTTPException(409, detail="document_busy")
        state["pending"][kind] = {"id": job, "at": now().isoformat()}
        return state["business"], state["consent"]["receipt"]

    business, receipt = transaction(store, key, start)
    try:
        decision = check_document(kind, extract(image), business, data_directory)
    except Exception:
        decision = {"status": "action", "reason": "ocr_unavailable", "source": "PaddleOCR"}

    def finish(_, case, state):
        if state["pending"].get(kind, {}).get("id") == job:
            state["pending"].pop(kind)
            if (state.get("consent") or {}).get("receipt") == receipt:
                state["docs"][kind] = decision
        return public(case, state, request.app.state.catalogue)

    return transaction(store, key, finish)


@router.post("/documents/{kind}")
def upload(request: Request, kind: Document, file: UploadFile = File()):
    key = require_role(request, "owner")
    store = store_for(request)
    quote_id = transaction(store, key, lambda _, case, state: (ensure_consent(state), case.quote.id)[1])
    content = file.file.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise HTTPException(413, detail="invalid_specimen")
    return process_document(request, key, kind, content, quote_id)


@router.post("/specimens/{applicant}/{kind}")
def use_specimen(request: Request, applicant: Literal["K08", "K11"], kind: Document):
    key = require_role(request, "owner")
    quote_id = transaction(store_for(request), key, lambda _, case, state: (ensure_consent(state), case.quote.id)[1])
    specimen = directory(request) / "kyc_documents" / f"{applicant}_{DOC_FILES[kind]}.png"
    if not specimen.is_file():
        raise HTTPException(404, detail="specimen_missing")
    return process_document(request, key, kind, specimen.read_bytes(), quote_id)


@router.post("/replacement")
def replacement(request: Request):
    key = require_role(request, "owner")

    def check(_, case, state):
        ensure_consent(state)
        if (norm(state.get("business", "")) != norm("Trinity Bake House Pvt Ltd")
                or state["docs"].get("address", {}).get("reason") != "stale_proof"):
            raise HTTPException(409, detail="replacement_unavailable")
        return case.quote.id

    quote_id = transaction(store_for(request), key, check)
    return process_document(request, key, "address", corrected_bill(), quote_id, replacement=True)


@router.delete("/documents/{kind}")
def discard(request: Request, kind: Document):
    key = require_role(request, "owner")

    def apply(_, case, state):
        ensure_consent(state)
        if state.get("order"):
            raise HTTPException(409, detail="order_already_placed")
        state["pending"].pop(kind, None)
        state["docs"].pop(kind, None)
        return public(case, state, request.app.state.catalogue)

    return transaction(store_for(request), key, apply)


STEPS = (
    ("payment_confirmed", 4),
    ("install_scheduled", 10),
    ("installation", 19),
    ("active", 29),
)


@router.post("/order")
def place(request: Request):
    key = require_role(request, "owner")
    catalogue = request.app.state.catalogue

    def apply(conn, case, state):
        if state.get("order"):
            return public(case, state, catalogue)
        if not ready(case, state, catalogue) or case.blocker:
            raise HTTPException(409, detail="order_gates")
        if case.quote.version != catalogue.version or case.quote.expires_at <= now():
            raise HTTPException(409, detail="quote_expired")
        order_id, stamp = uuid4(), now()
        state["order"] = {"id": str(order_id), "status": "payment_pending",
                          "lines": [{"sku": line.sku, "name": line.name, "state": "payment_pending"} for line in case.quote.lines],
                          "events": [{"step": "payment_pending", "at": stamp.isoformat()}]}
        return public(case, state, catalogue)

    def enqueue(conn, case, state):
        new_order = not state.get("order")
        result = apply(conn, case, state)
        if new_order:
            order = state["order"]
            for index, (step, seconds) in enumerate(STEPS, start=1):
                conn.execute(
                    "INSERT INTO order_outbox(order_id, ordinal, owner_key, step, due_at) VALUES (%s,%s,%s,%s,%s)",
                    (order["id"], index, key, step, now() + timedelta(seconds=seconds)),
                )
        return result

    return transaction(store_for(request), key, enqueue)


def tick_orders(store):
    with store._connect() as conn:
        due = conn.execute(
            "SELECT order_id, ordinal, owner_key, step FROM order_outbox AS job "
            "WHERE applied_at IS NULL AND due_at <= now() AND NOT EXISTS ("
            "SELECT 1 FROM order_outbox AS previous WHERE previous.order_id=job.order_id "
            "AND previous.ordinal<job.ordinal AND previous.applied_at IS NULL) "
            "ORDER BY due_at LIMIT 20 FOR UPDATE SKIP LOCKED",
        ).fetchall()
        for order_id, ordinal, owner, step in due:
            conn.execute("SELECT 1 FROM owner_cases WHERE owner_key=%s FOR UPDATE", (owner,))
            row = conn.execute("SELECT payload FROM activation_cases WHERE owner_key=%s", (owner,)).fetchone()
            if row and row[0].get("order", {}).get("id") == str(order_id):
                state = row[0]
                order = state["order"]
                order.update(status=step)
                for line in order["lines"]:
                    connectivity = line["sku"] in {"P01", "P02", "P04", "P09"}
                    if not connectivity and step in {"install_scheduled", "installation"}:
                        line["state"] = "active"
                    else:
                        line["state"] = step
                order["events"].append({"step": step, "at": now().isoformat()})
                conn.execute("UPDATE activation_cases SET payload=%s,updated_at=now() WHERE owner_key=%s",
                             (Jsonb(state), owner))
            conn.execute("UPDATE order_outbox SET applied_at=now() WHERE order_id=%s AND ordinal=%s",
                         (order_id, ordinal))


@router.post("/handoff")
def handoff(request: Request):
    key = require_role(request, "owner")

    def apply(_, case, state):
        ensure_consent(state)
        if not state.get("handoff"):
            state["handoff"] = {"id": str(uuid4()), "at": now().isoformat(),
                                "assignee": "partner-demo", "status": "queued", "source": "owner_self_service"}
        return public(case, state, request.app.state.catalogue)

    return transaction(store_for(request), key, apply)


def partner(request):
    require_role(request, "partner")
    if identity(request)["name"] != "partner-demo":
        raise HTTPException(403, detail="unassigned_partner")
    return store_for(request)


@router.get("/partner")
def queue(request: Request):
    with partner(request)._connect() as conn:
        rows = conn.execute(
            "SELECT active.payload, owner.payload FROM activation_cases active JOIN owner_cases owner USING(owner_key) "
            "WHERE active.payload->'handoff'->>'assignee'='partner-demo' AND "
            "active.payload->>'consent' IS NOT NULL AND active.quote_id::text=owner.payload->'quote'->>'id' "
            "ORDER BY active.updated_at DESC LIMIT 50",
        ).fetchall()
    return [{"id": data["handoff"]["id"], "reference": case["id"][:8].upper(),
             "status": data["handoff"]["status"], "source": data["handoff"]["source"],
             "blockers": [doc["reason"] for doc in data["docs"].values() if doc["status"] != "pass"],
             "monthly_paise": case["quote"]["monthly_paise"]} for data, case in rows]


@router.post("/partner/{assignment}/acknowledge")
def acknowledge(request: Request, assignment: str):
    with partner(request)._connect() as conn:
        result = conn.execute(
            "UPDATE activation_cases active SET payload=jsonb_set(active.payload,'{handoff,status}',to_jsonb('reviewed'::text)),updated_at=now() "
            "FROM owner_cases owner WHERE active.owner_key=owner.owner_key AND "
            "active.quote_id::text=owner.payload->'quote'->>'id' AND "
            "active.payload->'handoff'->>'id'=%s AND active.payload->'handoff'->>'assignee'='partner-demo' "
            "AND active.payload->>'consent' IS NOT NULL RETURNING active.owner_key", (assignment,),
        ).fetchone()
    if not result:
        raise HTTPException(404, detail="assignment_missing")
    return {"status": "reviewed"}
