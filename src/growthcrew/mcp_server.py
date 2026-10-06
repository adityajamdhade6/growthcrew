"""GrowthCrew as an MCP server, for Claude Desktop, Claude Code or any MCP client.

Run it with `growthcrew mcp serve --user you@example.com` (stdio). It acts as that GrowthCrew
user and sees only that user's workspaces.

Every tool is read-only except `propose_content`, which writes new drafts and so needs an
approval token: a short-lived, single-use token a person mints for one workspace and one
action (`growthcrew mcp approve` or the Settings page). Proposed drafts wait for approval
like any other; nothing here approves, schedules or publishes.
"""

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from growthcrew import audit, keys
from growthcrew.agents.strategist import load_latest_strategy
from growthcrew.analytics.analysis import analyze
from growthcrew.brain.store import WORKSPACES_DIR, list_versions, load_brain
from growthcrew.content.types import ContentRequest
from growthcrew.db.models import ApprovalTokenUse, Cycle, Draft, User
from growthcrew.memory import playbook
from growthcrew.monitor import signals
from growthcrew.naming import display, piece_name
from growthcrew.reports.learning_log import learning_log

TOKEN_TTL = 15 * 60
ACTIONS = ("propose_content",)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


class AccessDenied(ToolError):
    """Shown to the MCP client as the tool's error message."""


def mint_approval(workspace: str, action: str, person: str, ttl: int = TOKEN_TTL) -> str:
    """A token that lets an MCP client do `action` once in `workspace`, for `ttl` seconds."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Use one of: {', '.join(ACTIONS)}")
    return keys.sign(
        {"purpose": "mcp", "workspace": workspace, "action": action, "person": person,
         "nonce": secrets.token_hex(12)},
        ttl,
    )  # fmt: skip


def spend_approval(engine: Engine, token: str, workspace: str, action: str) -> str:
    """Check and use up a token. Returns the person who approved."""
    payload = keys.verify(token)
    if not payload or payload.get("purpose") != "mcp":
        raise AccessDenied(
            "The approval token is invalid or has expired; ask a person for a new one"
        )
    if payload["workspace"] != workspace or payload["action"] != action:
        raise AccessDenied(
            f"This token approves '{payload['action']}' in '{payload['workspace']}' only"
        )
    try:
        with Session(engine) as session:
            session.add(ApprovalTokenUse(nonce=payload["nonce"], workspace=workspace,
                                         action=action, approved_by=payload["person"]))  # fmt: skip
            session.commit()
    except IntegrityError as exc:
        raise AccessDenied("This approval token has already been used") from exc
    audit.record(engine, workspace, payload["person"], "mcp.approval_spent", action)
    return payload["person"]


def build_server(
    engine: Engine,
    user_email: str,
    root: Path = WORKSPACES_DIR,
    llm_factory=None,
) -> MCPServer:
    with Session(engine, expire_on_commit=False) as session:
        user = session.exec(select(User).where(User.email == user_email.lower())).first()
    if user is None:
        raise LookupError(f"No GrowthCrew user {user_email}; add one with `growthcrew user add`")

    def chance(probability: float | None) -> str:
        # Never claim certainty: the rule everywhere results are shown.
        if probability is None:
            return "unknown"
        return "over 99.9%" if probability > 0.999 else f"{probability:.1%}"

    def allowed(workspace: str) -> str:
        if not user.can_access(workspace):
            raise AccessDenied(f"{user.email} has no access to workspace '{workspace}'")
        if not list_versions(workspace, root):
            raise ToolError(f"No workspace '{workspace}'")
        return workspace

    server = MCPServer(
        name="growthcrew",
        title="GrowthCrew",
        instructions=(
            "GrowthCrew is a marketing team for a small business with a person approving every "
            "output. Use list_workspaces first. Experiment results always carry their "
            "uncertainty; report it, and never call a winner the tools do not call. Nothing "
            "can be approved or published from here."
        ),
    )

    @server.tool(annotations=READ_ONLY)
    def list_workspaces() -> list[dict]:
        """The businesses this user can see, with how many drafts wait for approval."""
        out = []
        with Session(engine) as session:
            for folder in sorted(p.name for p in root.glob("*") if p.is_dir()):
                if not user.can_access(folder) or not list_versions(folder, root):
                    continue
                pending = session.exec(
                    select(func.count(Draft.id)).where(
                        Draft.workspace == folder, Draft.status == "pending_approval"
                    )
                ).one()
                name = load_brain(folder, root=root).business.name or folder
                out.append({"workspace": folder, "name": name, "pending_approvals": pending})
        return out

    @server.tool(annotations=READ_ONLY)
    def what_did_we_learn(workspace: str) -> dict:
        """This week's learnings: what worked, what did not, and how the strategy changed."""
        log = learning_log(engine, allowed(workspace))
        weeks = [entry for entry in log if entry["kind"] == "learnings"]
        if not weeks:
            return {"workspace": workspace, "summary": "No week has been analysed yet."}
        latest = weeks[0]
        return {
            "workspace": workspace,
            "week": latest["window"],
            "what_worked": [item["statement"] for item in latest["what_worked"]],
            "what_did_not": [item["statement"] for item in latest["what_didnt"]],
            "changes": [
                {"change": c["change"], "ruling": display(c["decision"]), "reason": c["reason"]}
                for c in latest["changes"]
            ],
            "note": "Findings marked as descriptive come from comparisons that were not "
            "registered tests; they are leads, not results.",
        }

    @server.tool(annotations=READ_ONLY)
    def get_strategy(workspace: str) -> dict:
        """The current strategy: positioning, pillars, priorities, KPIs and planned tests."""
        doc = load_latest_strategy(allowed(workspace), root)
        return {
            "brand": doc.brand_name,
            "written": doc.created_at.date().isoformat(),
            "positioning": doc.positioning.model_dump(mode="json"),
            "content_pillars": [p.model_dump(mode="json") for p in doc.content_pillars],
            "icp_priorities": [p.model_dump(mode="json") for p in doc.icp_priorities],
            "kpis": [k.model_dump(mode="json") for k in doc.kpis],
            "experiments": [e.model_dump(mode="json") for e in doc.experiments],
            "open_issues": doc.issues,
        }

    @server.tool(annotations=READ_ONLY)
    def list_drafts(workspace: str, status: str = "pending_approval") -> list[dict]:
        """Drafts with this status: pending_approval, approved, rejected or blocked."""
        with Session(engine) as session:
            drafts = session.exec(
                select(Draft)
                .where(Draft.workspace == allowed(workspace), Draft.status == status)
                .order_by(Draft.id.desc())
                .limit(50)
            ).all()
        return [
            {"id": d.id, "piece": piece_name(d.piece_id), "type": display(d.content_type),
             "angle": d.angle, "passed_critic": d.passed_critic, "lowest_score": d.min_score,
             "text": d.text}
            for d in drafts
        ]  # fmt: skip

    @server.tool(annotations=READ_ONLY)
    def get_experiment_results(workspace: str) -> list[dict]:
        """Each experiment with its probability of being best, 95% interval and sample size."""
        result = analyze(engine, allowed(workspace))
        return [
            {
                "test": readout.title,
                "kind": readout.kind,
                "status": display(readout.status),
                "winner": readout.winner,
                "metric": readout.metric,
                "probability_leader_is_best": chance(
                    readout.uncertainty.prob_best.get(readout.uncertainty.leader)
                ),
                "lift_95_interval_pct": [
                    readout.uncertainty.lift_low_pct,
                    readout.uncertainty.lift_high_pct,
                ],  # fmt: skip
                "sample_size": readout.uncertainty.sample_size,
                "note": readout.note,
            }
            for readout in result.readouts
        ]

    @server.tool(annotations=READ_ONLY)
    def get_playbook(workspace: str) -> dict:
        """Patterns from the brand's own results. They are leads for tests, not proof."""
        return playbook.summary(engine, allowed(workspace))

    @server.tool(annotations=READ_ONLY)
    def get_signals(workspace: str) -> list[dict]:
        """This week's competitor, SEO and social findings, most important first, with sources."""
        return signals.inbox(engine, allowed(workspace), "new", 15)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    def propose_content(
        workspace: str,
        content_type: str,
        goal: str,
        pillar: str,
        audience: str,
        topic: str = "",
        funnel_stage: str = "awareness",
        approval_token: str = "",
    ) -> dict:
        """Write new drafts for a person to review. Needs an approval token from a person.

        The drafts go through the editor and guardrails, and wait for approval in the app.
        """
        allowed(workspace)
        person = spend_approval(engine, approval_token, workspace, "propose_content")
        if llm_factory is None:
            raise ToolError("No model is configured on this server")
        from growthcrew.agents.content import ContentAgent
        from growthcrew.agents.orchestrator import draft_from_piece
        from growthcrew.versions import strategy_version

        request = ContentRequest(content_type=content_type, pillar=pillar, audience=audience,
                                 funnel_stage=funnel_stage, goal=goal, topic=topic)  # fmt: skip
        brand = load_brain(workspace, root=root)
        strategy = load_latest_strategy(workspace, root)
        agent = ContentAgent(llm_factory(), root=root)
        records, _ = agent.produce(request, brand, strategy, f"mcp-{content_type}")
        with Session(engine, expire_on_commit=False) as session:
            origin = json.dumps({"source": "mcp", "approved_by": person})
            cycle = Cycle(
                workspace=workspace,
                week_start=datetime.now(UTC),
                stage="awaiting_approval",
                state_json=origin,
            )
            session.add(cycle)
            session.flush()
            version = strategy_version(workspace, root)
            rows = [draft_from_piece(r, cycle.id, workspace, version) for r in records]
            session.add_all(rows)
            session.commit()
        return {
            "drafts": [{"id": row.id, "status": row.status, "text": row.text} for row in rows],
            "note": "Waiting for a person to approve in GrowthCrew. Nothing was published.",
        }

    return server
