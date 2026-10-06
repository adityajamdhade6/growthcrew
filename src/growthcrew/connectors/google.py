"""Google Search Console and GA4, read-only, through OAuth.

Scopes are the read-only ones and nothing else. The refresh token is stored encrypted; the
access token is refreshed when it is within a minute of expiring.
"""

import os
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import Engine
from sqlmodel import Session, delete

from growthcrew import keys
from growthcrew.connectors import store
from growthcrew.db.models import KeywordRank

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GSC_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
GA4_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property}:runReport"
SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)
STATE_TTL = 15 * 60
# GA4 marks a missing dimension value like this.
NOT_SET = {"(not set)", "(other)", ""}


class GoogleNotConfigured(RuntimeError):
    pass


def _client_config() -> tuple[str, str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    base = os.getenv("GROWTHCREW_PUBLIC_URL", "http://localhost:3000").rstrip("/")
    if not client_id or not client_secret:
        raise GoogleNotConfigured(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set on the server, so Google "
            "cannot be connected"
        )
    return client_id, client_secret, f"{base}/api/connectors/google/callback"


def authorization_url(workspace: str, person: str) -> str:
    """Where to send the person to grant read-only access. The state ties the reply to them."""
    client_id, _, redirect = _client_config()
    state = keys.sign({"workspace": workspace, "person": person, "purpose": "google"}, STATE_TTL)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(query)}"


def finish(engine: Engine, code: str, state: str, client: httpx.Client) -> str:
    """Exchange the code for tokens and store them. Returns the workspace."""
    payload = keys.verify(state)
    if not payload or payload.get("purpose") != "google":
        raise ValueError("This sign-in link has expired or was not issued here; start again")
    client_id, client_secret, redirect = _client_config()
    response = client.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    tokens = response.json()
    granted = set(tokens.get("scope", "").split())
    extra = granted - set(SCOPES)
    if extra:
        # Least privilege: refuse a grant wider than asked for.
        raise ValueError(f"Google granted more access than requested: {', '.join(sorted(extra))}")
    store.save(
        engine,
        payload["workspace"],
        "google",
        payload["person"],
        secret={
            "refresh_token": tokens.get("refresh_token", ""),
            "access_token": tokens["access_token"],
            "expires_at": time.time() + tokens.get("expires_in", 3600),
        },
        scopes=" ".join(sorted(granted)),
    )
    return payload["workspace"]


def access_token(engine: Engine, workspace: str, client: httpx.Client) -> str:
    loaded = store.load(engine, workspace, "google")
    if loaded is None:
        raise LookupError("Google is not connected for this workspace")
    secret, _ = loaded
    if secret.get("expires_at", 0) - 60 > time.time():
        return secret["access_token"]
    if not secret.get("refresh_token"):
        raise ValueError("Google access has expired; reconnect it in Settings")
    client_id, client_secret, _ = _client_config()
    response = client.post(
        TOKEN_URL,
        data={
            "refresh_token": secret["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    tokens = response.json()
    store.save(engine, workspace, "google", "scheduler",
               secret={"access_token": tokens["access_token"],
                       "expires_at": time.time() + tokens.get("expires_in", 3600)})  # fmt: skip
    return tokens["access_token"]


def search_console(
    engine: Engine, workspace: str, client: httpx.Client, end: date, days: int = 7
) -> list[dict]:
    """Rows by date and page for content matching, and query positions for the SEO agent."""
    _, settings = store.load(engine, workspace, "google")
    site = settings.get("site_url")
    if not site:
        raise ValueError("Set the Search Console property (site URL) for this workspace")
    token = access_token(engine, workspace, client)
    url = GSC_URL.format(site=quote(site, safe=""))
    window = {"startDate": (end - timedelta(days=days - 1)).isoformat(), "endDate": end.isoformat()}

    def query(dimensions: list[str]) -> list[dict]:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={**window, "dimensions": dimensions, "rowLimit": 25000},
        )
        response.raise_for_status()
        return response.json().get("rows", [])

    rows = [
        {"refs": [keys_[1]], "date": _day(keys_[0]), "clicks": float(r.get("clicks", 0)),
         "impressions": float(r.get("impressions", 0))}
        for r in query(["date", "page"])
        if len(keys_ := r.get("keys", [])) == 2
    ]  # fmt: skip
    start = datetime.combine(end - timedelta(days=days - 1), datetime.min.time(), UTC)
    with Session(engine) as session:
        # Search Console revises recent days, so the window is replaced, not appended to.
        session.exec(
            delete(KeywordRank).where(KeywordRank.workspace == workspace, KeywordRank.date >= start)
        )
        for r in query(["date", "query", "page"]):
            day, keyword, page = r["keys"]
            session.add(KeywordRank(workspace=workspace, keyword=keyword.lower(), page=page,
                                    date=_day(day), position=float(r.get("position", 0)),
                                    clicks=float(r.get("clicks", 0)),
                                    impressions=float(r.get("impressions", 0))))  # fmt: skip
        session.commit()
    return rows


def ga4(
    engine: Engine, workspace: str, client: httpx.Client, end: date, days: int = 7
) -> list[dict]:
    """Sessions and key events by date, utm_content (the tracking key) and landing page."""
    _, settings = store.load(engine, workspace, "google")
    prop = str(settings.get("ga4_property", "")).removeprefix("properties/")
    if not prop.isdigit():
        raise ValueError("Set the GA4 property id (digits only) for this workspace")
    response = client.post(
        GA4_URL.format(property=prop),
        headers={"Authorization": f"Bearer {access_token(engine, workspace, client)}"},
        json={
            "dateRanges": [
                {
                    "startDate": (end - timedelta(days=days - 1)).isoformat(),
                    "endDate": end.isoformat(),
                }
            ],
            "dimensions": [
                {"name": "date"},
                {"name": "sessionManualAdContent"},
                {"name": "landingPage"},
            ],  # fmt: skip
            "metrics": [{"name": "sessions"}, {"name": "keyEvents"}],
            "limit": 10000,
        },
    )
    response.raise_for_status()
    rows = []
    for r in response.json().get("rows", []):
        day, content, page = (v.get("value", "") for v in r.get("dimensionValues", []))
        sessions, events = (float(v.get("value", 0) or 0) for v in r.get("metricValues", []))
        refs = [value for value in (content, page) if value not in NOT_SET]
        if refs:
            rows.append({"refs": refs, "date": _day(day), "sessions": sessions,
                         "conversions": events})  # fmt: skip
    return rows


def _day(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
