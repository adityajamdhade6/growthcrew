"""Phase 10: approving from the email digest and from Slack."""

import hashlib
import hmac
import json
import time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from growthcrew import approvals, budget
from growthcrew.api.auth import create_user
from growthcrew.connectors import store
from growthcrew.db.models import Cycle, Draft, User


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("GROWTHCREW_SECRET", "test-secret")
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Cycle(workspace="acme", week_start=budget.week_start(), stage="awaiting_approval")
        )
        session.add(
            Draft(
                cycle_id=1,
                workspace="acme",
                piece_id="01-ad",
                content_type="ad",
                original_text="Hi",
                text="Hi",
                body_json="{}",
                metadata_json="{}",
                min_score=9,
                passed_critic=True,
            )
        )
        session.commit()
    create_user(engine, "owner@acme.test", "correct-horse-battery", "acme")
    return engine


def owner(engine) -> User:
    with Session(engine) as session:
        return session.exec(select(User)).first()


def status(engine) -> str:
    with Session(engine) as session:
        return session.get(Draft, 1).status


def test_an_email_link_only_confirms_and_works_once(engine):
    token = approvals.approval_link(owner(engine), 1, "approved").rsplit("/", 1)[1]
    assert "Approve" in approvals.confirm_page(engine, token)
    assert status(engine) == "pending_approval"  # opening the link decides nothing
    approvals.use_link(engine, token)
    assert status(engine) == "approved"
    with pytest.raises(approvals.ApprovalRefused):
        approvals.use_link(engine, token)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.read_link(token[:-3] + "xyz")


def test_a_viewer_cannot_approve_from_email(engine):
    with Session(engine) as session:
        user = session.exec(select(User)).first()
        user.roles = json.dumps({"acme": "viewer"})
        session.add(user)
        session.commit()
    token = approvals.approval_link(owner(engine), 1, "approved").rsplit("/", 1)[1]
    with pytest.raises(approvals.ApprovalRefused):
        approvals.use_link(engine, token)
    assert status(engine) == "pending_approval"


def test_slack_presses_need_a_valid_fresh_signature_and_a_linked_user(engine):
    store.save(
        engine,
        "acme",
        "slack",
        "owner@acme.test",
        secret={"signing_secret": "s3cret", "webhook_url": "https://hooks.example"},
    )
    body = b"payload=x"
    now = time.time()
    sign = "v0=" + hmac.new(b"s3cret", f"v0:{int(now)}:x".encode(), hashlib.sha256).hexdigest()
    good = (
        "v0="
        + hmac.new(b"s3cret", f"v0:{int(now)}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    )
    approvals.verify_slack(engine, "acme", str(int(now)), body, good, now=now)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.verify_slack(engine, "acme", str(int(now)), body, sign, now=now)
    with pytest.raises(approvals.ApprovalRefused):
        approvals.verify_slack(engine, "acme", str(int(now)), body, good, now=now + 600)
    press = {
        "user": {"id": "U1"},
        "actions": [{"value": json.dumps({"w": "acme", "d": 1, "x": "rejected"})}],
    }
    with pytest.raises(approvals.ApprovalRefused):
        approvals.slack_action(engine, press)
    approvals.link_slack_user(engine, "acme", "owner@acme.test", "U1")
    approvals.slack_action(engine, press)
    assert status(engine) == "rejected"
