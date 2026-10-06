"""Simple auth: email and password, a signed bearer token, and per-user workspace access.

Users are created from the command line (`growthcrew user add`); there is no open sign-up.
"""

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import keys
from growthcrew.api.deps import engine_dep
from growthcrew.db.models import CalendarItem, Cycle, Draft, Signal, User

TOKEN_TTL = 7 * 24 * 3600
# Path parameters that name a row; the route belongs to that row's workspace.
ID_PARAMS = (
    ("draft_id", Draft),
    ("cycle_id", Cycle),
    ("item_id", CalendarItem),
    ("signal_id", Signal),
)
# The Google sign-in callback is reached by the browser without a token; its signed state
# carries the workspace and the person who started it.
PUBLIC_PATHS = {"/health", "/auth/login", "/docs", "/openapi.json", "/connectors/google/callback"}
# Routes with no workspace in them that only an admin may use.
ADMIN_PREFIXES = ("/costs", "/settings")
# After this many wrong passwords for one email, sign-in is refused for LOCKOUT_SECONDS.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
# email -> (failures, time of the last one). In memory: resets on restart, one process only.
_failures: dict[str, tuple[int, float]] = {}

router = APIRouter()


def _secret() -> bytes:
    return keys.secret()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _, digest = stored.partition("$")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


def make_token(user_id: int, ttl: int = TOKEN_TTL) -> str:
    payload = f"{user_id}.{int(time.time()) + ttl}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()


def read_token(token: str) -> int | None:
    """The user id in a valid, unexpired token, else None."""
    try:
        user_id, expires, signature = base64.urlsafe_b64decode(token).decode().split(".")
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(_secret(), f"{user_id}.{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected) or int(expires) < time.time():
        return None
    return int(user_id)


def create_user(engine: Engine, email: str, password: str, workspaces: str) -> User:
    if len(password) < 10:
        raise ValueError("Use a password of at least 10 characters")
    with Session(engine, expire_on_commit=False) as session:
        if session.exec(select(User).where(User.email == email)).first():
            raise ValueError(f"A user with email {email} already exists")
        user = User(email=email, password_hash=hash_password(password), workspaces=workspaces)
        session.add(user)
        session.commit()
        return user


def grant(engine: Engine, user: User, workspace: str) -> None:
    """Give a user access to a workspace they just created."""
    if user.can_access(workspace):
        return
    with Session(engine) as session:
        row = session.get(User, user.id)
        row.workspaces = ",".join(filter(None, [*row.workspaces.split(","), workspace]))
        session.add(row)
        session.commit()


def authorize(request: Request, engine: Engine = Depends(engine_dep)) -> None:
    """Applied to every route: require a valid token and access to the workspace involved."""
    path = request.url.path
    if path in PUBLIC_PATHS:
        return
    header = request.headers.get("authorization", "")
    user_id = read_token(header.removeprefix("Bearer ").strip()) if header else None
    with Session(engine, expire_on_commit=False) as session:
        user = session.get(User, user_id) if user_id else None
        if user is None:
            raise HTTPException(401, "Sign in to continue")
        workspace = request.path_params.get("workspace") or request.query_params.get("workspace")
        # Routes addressed by id belong to the workspace of the row they name.
        for param, model in ID_PARAMS:
            if param in request.path_params:
                row = session.get(model, int(request.path_params[param]))
                if row is None:
                    raise HTTPException(404, f"{model.__name__} not found")
                workspace = row.workspace
    if workspace and not user.can_access(workspace):
        raise HTTPException(403, "You do not have access to this workspace")
    if not workspace and path.startswith(ADMIN_PREFIXES) and not user.is_admin:
        raise HTTPException(403, "Only an admin can do this")
    request.state.user = user


def current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


class LoginIn(BaseModel):
    email: str
    password: str


def _profile(user: User) -> dict:
    return {"email": user.email, "is_admin": user.is_admin}


@router.post("/auth/login")
def login(body: LoginIn, engine: Engine = Depends(engine_dep)) -> dict:
    email = body.email.strip().lower()
    count, last = _failures.get(email, (0, 0.0))
    if count >= MAX_FAILURES and time.time() - last < LOCKOUT_SECONDS:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    with Session(engine, expire_on_commit=False) as session:
        user = session.exec(select(User).where(User.email == email)).first()
    # Same message either way, so the response does not reveal which emails exist.
    if user is None or not verify_password(body.password, user.password_hash):
        expired = time.time() - last >= LOCKOUT_SECONDS
        _failures[email] = (1 if expired else count + 1, time.time())
        raise HTTPException(401, "Wrong email or password")
    _failures.pop(email, None)
    return {"token": make_token(user.id), "user": _profile(user)}


@router.get("/auth/me")
def me(request: Request) -> dict:
    return _profile(current_user(request))
