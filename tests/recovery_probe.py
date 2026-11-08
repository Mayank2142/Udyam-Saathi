"""Reproduce the slide evidence against the isolated Compose stack."""
import json
import os
import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import monotonic, sleep
from unittest.mock import patch

import httpx
from psycopg.errors import UniqueViolation

from app.activation import tick_orders
from app.store import PostgresStore
from app.verification import BUCKET, extract, s3


smoke = runpy.run_path(str(Path(__file__).with_name("smoke.py")))
post = smoke["post"]
sign_in = smoke["sign_in"]
base = "http://127.0.0.1:8174"
store = PostgresStore(os.environ["DATABASE_URL"])
needs = {
    "segment": "office", "employees": 5, "sites": 1, "city_tier": 2, "speed": 300,
    "email_seats": 5, "devices": 5, "monthly_budget": 3000, "coverage": "fibre",
}
consent = {"accepted": True, "notice": "saathi-synthetic-v1", "language": "en",
           "business": "Trinity Bake House Pvt Ltd"}
route = "/api/activation"


def client():
    return smoke["demo_client"](base, 180)


def prepare(owner):
    current = owner.get("/api/case").raise_for_status().json()
    case = owner.put("/api/case", json={"revision": current["revision"] if current else 0,
                                      "needs": needs}).raise_for_status().json()
    assert not case["blocker"] and case["quote"]["monthly_paise"] == 261900
    accepted = post(owner, "/api/case/accept", {"revision": case["revision"], "quote_id": case["quote"]["id"]})
    assert accepted["accepted_at"]
    assert post(owner, route + "/consent", consent)["consented"]
    return accepted


def time_recovery(owner, partner):
    quote = prepare(owner)
    signatory = post(owner, route + "/specimens/K11/signatory")["docs"]["signatory"]
    assert signatory["status"] == "pass"
    stale = post(owner, route + "/specimens/K11/address")["docs"]["address"]
    assert stale["reason"] == "stale_proof" and stale["age_days"] == 257
    started = monotonic()
    assert owner.post(route + "/order").status_code == 409
    handoff = post(owner, route + "/handoff")["handoff"]
    entries = partner.get(route + "/partner").raise_for_status().json()
    assert any(item["id"] == handoff["id"] and "stale_proof" in item["blockers"] for item in entries)
    assert post(partner, f"{route}/partner/{handoff['id']}/acknowledge")["status"] == "reviewed"
    corrected = post(owner, route + "/replacement")
    assert corrected["ready"] and corrected["handoff"]["status"] == "reviewed"
    assert corrected["docs"]["address"]["age_days"] == 27 and corrected["docs"]["signatory"] == signatory
    saved = owner.get("/api/case").raise_for_status().json()
    assert saved["quote"]["id"] == quote["quote"]["id"] and saved["accepted_at"] == quote["accepted_at"]
    return {"seconds": round(monotonic() - started, 2), "staff_acknowledgements": 1,
            "prior_valid_fields_or_files_resubmitted": 0, "accepted_quote_retained": True,
            "next_action": "ready_for_simulated_checkout"}


def probe_outbox(owner):
    order = post(owner, route + "/order")["order"]
    order_id = order["id"]
    with store._connect() as conn:
        conn.execute("UPDATE order_outbox SET due_at = now() + interval '5 minutes' WHERE order_id = %s",
                     (order_id,))
        conn.execute("UPDATE order_outbox SET due_at = now() WHERE order_id = %s AND ordinal = 2", (order_id,))

    def release(ordinal):
        with store._connect() as conn:
            conn.execute("UPDATE order_outbox SET due_at = now() WHERE order_id = %s AND ordinal = %s",
                         (order_id, ordinal))
        tick_orders(store)

    def state():
        return owner.get(route).raise_for_status().json()["order"]

    tick_orders(store)
    assert state()["status"] == "payment_pending"
    try:
        with store._connect() as conn:
            conn.execute("INSERT INTO order_outbox(order_id, ordinal, owner_key, step, due_at) "
                         "SELECT order_id, ordinal, owner_key, step, due_at FROM order_outbox "
                         "WHERE order_id = %s AND ordinal = 1", (order_id,))
    except UniqueViolation:
        pass
    else:
        raise AssertionError("Duplicate outbox key was accepted")
    release(1)
    release(2)
    partial = state()
    line_states = {line["sku"]: line["state"] for line in partial["lines"]}
    assert line_states == {"P02": "install_scheduled", "P16": "active", "P20": "active"}, partial
    assert post(owner, route + "/order")["order"]["id"] == order_id
    release(3)
    release(4)
    tick_orders(store)
    completed = state()
    assert completed["id"] == order_id and completed["status"] == "active"
    events = [event["step"] for event in completed["events"]]
    assert events == ["payment_pending", "payment_confirmed", "install_scheduled", "installation", "active"]
    return {"duplicate_db_key_rejected": True, "later_job_waited_for_prior": True,
            "held_fibre_line_states": line_states, "same_order_on_retry": True, "event_sequence": events}


def probe_withdrawal(owner, partner):
    prepare(owner)
    handoff = post(owner, route + "/handoff")["handoff"]
    assert any(item["id"] == handoff["id"] for item in partner.get(route + "/partner").raise_for_status().json())
    with client() as uploader, ThreadPoolExecutor(max_workers=1) as worker:
        uploader.cookies.update(owner.cookies)
        running = worker.submit(post, uploader, route + "/specimens/K11/signatory")
        for _ in range(50):
            if "signatory" in owner.get(route).raise_for_status().json()["pending"]:
                break
            sleep(.025)
        else:
            raise AssertionError("No in-flight OCR was observed")
        withdrawn = post(owner, route + "/consent", {**consent, "accepted": False})
        assert not withdrawn["consented"]
        running.result()
    final = owner.get(route).raise_for_status().json()
    assert not final["consented"] and not final["docs"] and not final["pending"] and not final["handoff"]
    assert all(item["id"] != handoff["id"] for item in partner.get(route + "/partner").raise_for_status().json())
    assert partner.post(f"{route}/partner/{handoff['id']}/acknowledge").status_code == 404
    storage = s3()
    assert storage.list_objects_v2(Bucket=BUCKET, Prefix="temp/")["KeyCount"] == 0

    def fail_ocr(*args, **kwargs):
        assert storage.list_objects_v2(Bucket=BUCKET, Prefix="temp/")["KeyCount"] == 1
        raise httpx.ReadTimeout("Injected OCR timeout")

    with patch("app.verification.httpx.post", side_effect=fail_ocr):
        try:
            extract(b"synthetic failure probe")
        except httpx.ReadTimeout:
            pass
        else:
            raise AssertionError("The injected OCR error did not occur")
    assert storage.list_objects_v2(Bucket=BUCKET, Prefix="temp/")["KeyCount"] == 0
    return {"withdrawn_while_ocr_pending": True, "late_result_discarded": True,
            "partner_queue_and_old_link_revoked": True, "temporary_object_deleted_after_error": True}


with client() as owner, client() as partner, client() as other:
    assert sign_in(owner, "owner-try", "saathi-owner-try")["role"] == "owner"
    assert sign_in(other, "owner-demo", "saathi-owner-demo")["role"] == "owner"
    assert sign_in(partner, "partner-demo", "saathi-partner-demo")["role"] == "partner"
    trials = [time_recovery(owner, partner) for _ in range(3)]
    result = {
        "recovery_trials": trials, "median_seconds": median(run["seconds"] for run in trials),
        "outbox": probe_outbox(owner), "privacy": probe_withdrawal(other, partner),
    }
    print(json.dumps(result, indent=2))
