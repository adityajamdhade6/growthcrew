"""The append-only audit log: who approved, edited, published or changed access, and when.

Rows are chained by hash (each includes the previous row's hash), and the database refuses
UPDATE and DELETE on the table (triggers installed by `protect`), so the record cannot be
quietly rewritten. `verify` walks the chain and reports the first break.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import Engine, text
from sqlmodel import Session, select

from growthcrew.db.models import AuditLog


def _digest(prev: str, at: datetime, workspace: str, actor: str, action: str, target: str,
            detail: str) -> str:  # fmt: skip
    body = "|".join([prev, at.isoformat(), workspace, actor, action, target, detail])
    return hashlib.sha256(body.encode()).hexdigest()


def record(engine: Engine, workspace: str, actor: str, action: str, target: str = "",
           **detail) -> AuditLog:  # fmt: skip
    with Session(engine, expire_on_commit=False) as session:
        last = session.exec(select(AuditLog).order_by(AuditLog.id.desc())).first()
        prev = last.hash if last else ""
        at = datetime.now(UTC).replace(microsecond=0)
        body = json.dumps(detail, sort_keys=True, default=str)
        row = AuditLog(at=at, workspace=workspace, actor=actor, action=action, target=target,
                       detail=body, prev_hash=prev,
                       hash=_digest(prev, at, workspace, actor, action, target, body))  # fmt: skip
        session.add(row)
        session.commit()
        return row


def entries(engine: Engine, workspace: str, limit: int = 200) -> list[dict]:
    with Session(engine) as session:
        rows = session.exec(
            select(AuditLog).where(AuditLog.workspace == workspace)
            .order_by(AuditLog.id.desc()).limit(limit)
        ).all()  # fmt: skip
    return [{"at": r.at.isoformat(), "actor": r.actor, "action": r.action, "target": r.target,
             "detail": json.loads(r.detail)} for r in rows]  # fmt: skip


def verify(engine: Engine) -> int | None:
    """The id of the first row whose hash does not match, or None if the chain is intact."""
    prev = ""
    with Session(engine) as session:
        for row in session.exec(select(AuditLog).order_by(AuditLog.id)):
            at = row.at if row.at.tzinfo else row.at.replace(tzinfo=UTC)
            expected = _digest(prev, at, row.workspace, row.actor, row.action, row.target,
                               row.detail)  # fmt: skip
            if row.prev_hash != prev or row.hash != expected:
                return row.id
            prev = row.hash
    return None


def protect(engine: Engine) -> None:
    """Make the audit table append-only in the database itself."""
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            for verb in ("UPDATE", "DELETE"):
                connection.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS auditlog_no_{verb.lower()} BEFORE {verb} "
                        "ON auditlog BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END"
                    )
                )
        elif engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "CREATE OR REPLACE FUNCTION auditlog_append_only() RETURNS trigger AS $$ "
                    "BEGIN RAISE EXCEPTION 'the audit log is append-only'; END $$ LANGUAGE plpgsql"
                )
            )
            connection.execute(text("DROP TRIGGER IF EXISTS auditlog_append_only ON auditlog"))
            connection.execute(text(
                "CREATE TRIGGER auditlog_append_only BEFORE UPDATE OR DELETE ON auditlog "
                "FOR EACH ROW EXECUTE FUNCTION auditlog_append_only()"))  # fmt: skip
