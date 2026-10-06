"""Per-workspace credentials, encrypted at rest."""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session, select

from growthcrew import keys
from growthcrew.db.models import ConnectorCredential, SyncRun

PROVIDERS = ("google", "brevo", "hubspot", "mcp")
# Settings each provider may store, and which of them are secret.
SETTINGS = {
    "google": {"site_url", "ga4_property"},
    "brevo": {"list_id", "sender_name", "sender_email", "postal_address"},
    "hubspot": set(),
    "mcp": {"url", "tool", "arguments", "source", "fields"},
}


def _row(session: Session, workspace: str, provider: str) -> ConnectorCredential | None:
    return session.exec(
        select(ConnectorCredential).where(
            ConnectorCredential.workspace == workspace, ConnectorCredential.provider == provider
        )
    ).first()


def save(
    engine: Engine,
    workspace: str,
    provider: str,
    person: str,
    secret: dict | None = None,
    settings: dict | None = None,
    scopes: str | None = None,
) -> None:
    """Create or update a connection. Only the parts given are changed."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown source '{provider}'. Use one of: {', '.join(PROVIDERS)}")
    unknown = set(settings or {}) - SETTINGS[provider]
    if unknown:
        raise ValueError(f"Unknown settings for {provider}: {', '.join(sorted(unknown))}")
    with Session(engine) as session:
        row = _row(session, workspace, provider) or ConnectorCredential(
            workspace=workspace, provider=provider
        )
        if secret is not None:
            current = keys.decrypt(row.secret) if row.secret else {}
            row.secret = keys.encrypt({**current, **secret})
        if settings is not None:
            row.settings = json.dumps({**json.loads(row.settings or "{}"), **settings})
        if scopes is not None:
            row.scopes = scopes
        row.connected_by = person
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


def load(engine: Engine, workspace: str, provider: str) -> tuple[dict, dict] | None:
    """(secret, settings) for a connection, or None if it is not connected."""
    with Session(engine) as session:
        row = _row(session, workspace, provider)
    if row is None or not row.secret:
        return None
    return keys.decrypt(row.secret), json.loads(row.settings or "{}")


def remove(engine: Engine, workspace: str, provider: str) -> bool:
    with Session(engine) as session:
        row = _row(session, workspace, provider)
        if row is None:
            return False
        session.delete(row)
        session.commit()
    return True


def connected(engine: Engine, workspace: str) -> list[str]:
    with Session(engine) as session:
        return [
            row.provider
            for row in session.exec(
                select(ConnectorCredential).where(ConnectorCredential.workspace == workspace)
            )
            if row.secret
        ]


def status(engine: Engine, workspace: str) -> list[dict]:
    """What the Settings page shows: never a secret, only whether one is stored."""
    with Session(engine) as session:
        rows = {
            row.provider: row
            for row in session.exec(
                select(ConnectorCredential).where(ConnectorCredential.workspace == workspace)
            )
        }
        out = []
        for provider in PROVIDERS:
            row = rows.get(provider)
            last = session.exec(
                select(SyncRun)
                .where(SyncRun.workspace == workspace, SyncRun.provider == provider)
                .order_by(SyncRun.started_at.desc())
            ).first()
            out.append(
                {
                    "provider": provider,
                    "connected": bool(row and row.secret),
                    "settings": json.loads(row.settings) if row else {},
                    "scopes": row.scopes if row else "",
                    "connected_by": row.connected_by if row else "",
                    "last_sync": {
                        "at": last.started_at.isoformat(),
                        "status": last.status,
                        "rows": last.rows,
                        "matched": last.matched,
                        "error": last.error,
                    }
                    if last
                    else None,
                }
            )
    return out
