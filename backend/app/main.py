from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .activation import router as activation_router, tick_orders
from .advisor import router as advisor_router
from .auth import identity, router as auth_router
from .catalogue import Catalogue
from .domain import Accept, Case, Configure, UnavailableSelection, build_case
from .store import OrderExists, PostgresStore, PreviewStore, RevisionConflict


BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DATA = BACKEND.parents[1] / "6ae1873397bc1_digital" / "YTL_TrackA_Udyam_Digital_Data"
COOKIE = "saathi_owner"
LOCAL_ORIGINS = {
    f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (5173, 8174)
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = Path(os.environ.get("SAATHI_DATA_DIR", str(DEFAULT_DATA))) / "product_catalogue.csv"
    app.state.catalogue = Catalogue(path)
    app.state.data_directory = path.parent
    database = os.environ.get("DATABASE_URL")
    if database:
        app.state.store = PostgresStore(database)
        app.state.storage = "postgres"
    elif os.environ.get("SAATHI_PREVIEW") == "1":
        app.state.store = PreviewStore()
        app.state.storage = "preview"
    else:
        raise RuntimeError("Set DATABASE_URL or opt in to local ephemeral mode with SAATHI_PREVIEW=1.")
    async def run_outbox():
        while True:
            await asyncio.sleep(2)
            try:
                await asyncio.to_thread(tick_orders, app.state.store)
            except Exception:
                logging.getLogger(__name__).exception("Sandbox order event worker failed")

    task = asyncio.create_task(run_outbox()) if database else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Udyam Saathi", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


def owner_key(request: Request, response: Response) -> str:
    origin = request.headers.get("origin")
    if request.method != "GET" and origin and origin not in LOCAL_ORIGINS:
        raise HTTPException(403, detail="origin_not_allowed")
    token = request.cookies.get(COOKIE, "")
    person = identity(request)
    if person:
        if person["role"] != "owner":
            raise HTTPException(403, detail="owner_only")
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) and isinstance(request.app.state.store, PostgresStore):
            request.app.state.store.claim(hashlib.sha256(token.encode()).hexdigest(), person["key"])
            response.delete_cookie(COOKIE)
        response.headers["Cache-Control"] = "no-store"
        return person["key"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        token = secrets.token_urlsafe(32)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=7 * 86400)
    response.headers["Cache-Control"] = "no-store"
    return hashlib.sha256(token.encode()).hexdigest()


Owner = Annotated[str, Depends(owner_key)]


@app.get("/api/health")
def health(request: Request):
    return {
        "status": "ok",
        "storage": request.app.state.storage,
        "catalogue_products": len(request.app.state.catalogue.products),
    }


@app.get("/api/case", response_model=Case | None)
def get_case(request: Request, owner: Owner):
    return request.app.state.store.read(owner)


@app.put("/api/case", response_model=Case)
def configure_case(request: Request, config: Configure, owner: Owner):
    def update(current: Case | None) -> Case:
        if (current.revision if current else 0) != config.revision:
            raise RevisionConflict()
        return build_case(request.app.state.catalogue, config, current)

    try:
        return request.app.state.store.change(owner, update)
    except RevisionConflict as error:
        raise HTTPException(409, detail="case_changed") from error
    except UnavailableSelection as error:
        raise HTTPException(422, detail=str(error)) from error
    except OrderExists as error:
        raise HTTPException(409, detail="order_already_placed") from error


@app.post("/api/case/accept", response_model=Case)
def accept_quote(request: Request, acceptance: Accept, owner: Owner):
    def update(current: Case | None) -> Case:
        if not current or not current.quote or current.quote.id != acceptance.quote_id:
            raise HTTPException(409, detail="quote_changed")
        if current.accepted_at:
            return current
        if current.revision != acceptance.revision:
            raise HTTPException(409, detail="quote_changed")
        if current.blocker:
            raise HTTPException(422, detail=current.blocker)
        now = datetime.now(timezone.utc)
        if current.quote.expires_at <= now or current.quote.version != request.app.state.catalogue.version:
            raise HTTPException(409, detail="quote_expired")
        return current.model_copy(update={"accepted_at": now, "revision": current.revision + 1})

    return request.app.state.store.change(owner, update)


app.include_router(auth_router)
app.include_router(activation_router)
app.include_router(advisor_router)

DIST = Path(os.environ.get("SAATHI_WEB_DIST", str(BACKEND.parent / "web" / "dist")))
if (DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
