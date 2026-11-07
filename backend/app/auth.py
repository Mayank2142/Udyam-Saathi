import base64
import hashlib
import os
import secrets
from functools import lru_cache
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/api")
APP_URL = "http://127.0.0.1:8174"
ISSUER = os.getenv("OIDC_ISSUER", "")
INTERNAL = os.getenv("OIDC_INTERNAL", ISSUER)
CLIENT = "saathi-web"
TOKEN = "saathi_oidc"


@lru_cache(maxsize=1)
def keys():
    return jwt.PyJWKClient(f"{INTERNAL}/protocol/openid-connect/certs", timeout=5)


def identity(request: Request):
    if not ISSUER or not (token := request.cookies.get(TOKEN)):
        return None
    try:
        key = keys().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=ISSUER,
                            audience="saathi-api", options={"require": ["exp", "sub", "iss"]})
    except jwt.PyJWTError:
        return None
    roles = claims.get("realm_access", {}).get("roles", [])
    role = "partner" if "partner" in roles else "owner" if "owner" in roles else None
    if role is None or claims.get("azp") != CLIENT:
        return None
    owner_key = hashlib.sha256(f"{ISSUER}:{claims['sub']}".encode()).hexdigest()
    return {"key": owner_key, "role": role, "name": claims.get("preferred_username", role)}


def require_role(request: Request, role: str):
    person = identity(request)
    if not person or person["role"] != role:
        raise HTTPException(401, detail="sign_in_required")
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD"} and origin and origin != APP_URL:
        raise HTTPException(403, detail="origin_not_allowed")
    return person["key"]


@router.get("/session")
def session(request: Request, response: Response):
    person = identity(request)
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": bool(ISSUER), "role": person["role"] if person else None,
            "name": person["name"] if person else None}


@router.get("/auth/login")
def login():
    if not ISSUER:
        raise HTTPException(503, detail="identity_unavailable")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    query = urlencode({"client_id": CLIENT, "redirect_uri": APP_URL + "/api/auth/callback",
                       "response_type": "code", "scope": "openid profile", "state": state,
                       "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login"})
    response = RedirectResponse(f"{ISSUER}/protocol/openid-connect/auth?{query}")
    response.set_cookie("saathi_pkce", state + "." + verifier, path="/api/auth", max_age=300,
                        httponly=True, samesite="lax")
    return response


@router.get("/auth/callback")
def callback(request: Request, code: str, state: str):
    expected, _, verifier = request.cookies.get("saathi_pkce", "").partition(".")
    if not verifier or not secrets.compare_digest(expected, state):
        raise HTTPException(400, detail="invalid_login_state")
    try:
        token = httpx.post(f"{INTERNAL}/protocol/openid-connect/token", data={
            "grant_type": "authorization_code", "client_id": CLIENT, "code": code,
            "redirect_uri": APP_URL + "/api/auth/callback", "code_verifier": verifier,
        }, timeout=10).raise_for_status().json()
    except httpx.HTTPError as error:
        raise HTTPException(502, detail="identity_unavailable") from error
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("saathi_pkce", path="/api/auth")
    response.set_cookie(TOKEN, token["access_token"], max_age=min(int(token["expires_in"]), 3600),
                        path="/api", httponly=True, samesite="strict")
    return response


@router.post("/auth/logout")
def logout(request: Request):
    if request.headers.get("origin") != APP_URL:
        raise HTTPException(403, detail="origin_not_allowed")
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(TOKEN, path="/api")
    response.delete_cookie("saathi_owner")
    return response
