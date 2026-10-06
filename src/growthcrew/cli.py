"""growthcrew command line."""

import argparse
import json
import logging
import sys
from pathlib import Path

from growthcrew.brain.models import FIELD_PATHS, Brain, Competitor, FieldMeta
from growthcrew.brain.onboarding import QUESTIONS, Questionnaire, onboard
from growthcrew.brain.store import confirm_fields, load_brain


def ask_questionnaire() -> Questionnaire:
    print("A few questions (press Enter to skip any):")
    answers: dict = {}
    for name, question in QUESTIONS.items():
        reply = input(f"  {question} ").strip()
        if not reply:
            continue
        if name == "competitors":
            pairs = (item.partition("=") for item in reply.split(","))
            answers[name] = [Competitor(name=n.strip(), url=u.strip()) for n, _, u in pairs]
        else:
            answers[name] = reply
    return Questionnaire.model_validate(answers)


def print_brain(brain: Brain) -> None:
    print(f"\n{brain.workspace} · v{brain.version} · {len(brain.pages_crawled)} pages crawled")
    for path in FIELD_PATHS:
        meta = brain.fields.get(path, FieldMeta())
        value = brain.get(path)
        shown = value if isinstance(value, str) else json.dumps(_plain(value), ensure_ascii=False)
        print(f"\n{path}  [{meta.status}, {meta.confidence}]\n  {shown or '(empty)'}")
    print("\nWeakest fields (review these first):")
    for path, reason in brain.weakest():
        print(f"  - {path}: {reason}")


def _plain(value):
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value.model_dump() if hasattr(value, "model_dump") else value


def _llm():
    from growthcrew.llm import LLM

    llm = LLM()
    client = llm.client
    if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
        print("No Anthropic credentials found. Add ANTHROPIC_API_KEY to .env and retry.")
        return None
    return llm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growthcrew")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("onboard", help="Draft a brand brain from a website")
    p.add_argument("--url", required=True)
    p.add_argument("--workspace", help="Defaults to the site's domain name")
    p.add_argument("--answers", type=Path, help="JSON file of questionnaire answers")
    p.add_argument("--no-input", action="store_true", help="Skip the questionnaire")

    p = commands.add_parser("research", help="Research competitors, customers and market")
    p.add_argument("workspace")
    p.add_argument("--focus", default="")
    p.add_argument("--max-tool-calls", type=int)

    p = commands.add_parser("strategy", help="Draft a strategy from the brain and research")
    p.add_argument("workspace")
    p.add_argument("--no-pdf", action="store_true")

    p = commands.add_parser("content", help="Write a content batch through the critic loop")
    p.add_argument("workspace")
    p.add_argument("--weeks", type=int, default=2)
    p.add_argument("--max-items", type=int, default=8)

    p = commands.add_parser("monitor", help="Run the competitor, SEO and social monitors")
    p.add_argument("workspace")

    p = commands.add_parser("creative", help="Render an ad draft as images, with the vision critic")
    p.add_argument("workspace")
    p.add_argument("--draft", type=int, required=True, help="The ad draft's id")

    p = commands.add_parser("landing", help="Export approved landing hero variants as HTML")
    p.add_argument("workspace")
    p.add_argument("--draft", type=int, required=True, help="Any variant's draft id")

    p = commands.add_parser("mcp", help="Serve GrowthCrew over MCP, or mint an approval token")
    p.add_argument("action", choices=["serve", "approve"])
    p.add_argument("--user", required=True, help="The GrowthCrew user it acts as, or approves as")
    p.add_argument("--workspace", help="approve: the workspace the token is for")
    p.add_argument("--allow", default="propose_content", help="approve: the action allowed")

    p = commands.add_parser("connect", help="Store a source's API key and settings (encrypted)")
    p.add_argument("workspace")
    p.add_argument("provider", choices=["brevo", "hubspot", "mcp", "google"])
    p.add_argument("--key-env", help="Name of the environment variable holding the API key")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")

    p = commands.add_parser("sync", help="Pull fresh metrics from every connected source")
    p.add_argument("workspace")

    p = commands.add_parser("scheduler", help="Daily metrics sync and weekly analysis")
    p.add_argument("--loop", action="store_true", help="Keep running, checking every hour")
    p.add_argument("--analyse", action="store_true", help="Also run the weekly analyst")

    p = commands.add_parser("cycle", help="Run this week's cycle up to the approval stage")
    p.add_argument("workspace")
    p.add_argument("--max-items", type=int, default=5)

    p = commands.add_parser("user", help="Add a user who can sign in to the web app")
    p.add_argument("action", choices=["add"])
    p.add_argument("email")
    p.add_argument("--workspaces", default="", help="Comma-separated, or * for all (admin)")

    p = commands.add_parser("pilot", help="Set up, track and report on a 60-day pilot")
    p.add_argument("action", choices=["init", "track", "report", "testimonial"])
    p.add_argument("workspace")
    p.add_argument("--start", help="init: first day of the pilot, YYYY-MM-DD")
    p.add_argument("--business", help="init: the business's name")
    p.add_argument("--owner", default="", help="init: the owner's name")
    p.add_argument("--day", type=int, choices=[30, 60], help="report: which report")
    p.add_argument("--quote-file", type=Path, help="testimonial: file with the owner's words")
    p.add_argument("--name", default="")
    p.add_argument("--role", default="")
    p.add_argument("--attribution", choices=["full_name", "first_name_only", "anonymous"])
    p.add_argument(
        "--allow", default="", help="testimonial: uses the owner allowed, comma-separated"
    )
    p.add_argument("--confirmed", action="store_true", help="the owner approved this exact wording")

    p = commands.add_parser("show", help="Print a workspace's brain")
    p.add_argument("workspace")
    p.add_argument("--version", type=int)

    p = commands.add_parser("confirm", help="Mark brain fields as confirmed by a human")
    p.add_argument("workspace")
    p.add_argument("fields", nargs="+", metavar="FIELD", help="e.g. business.pricing")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if args.command == "onboard":
        if args.answers:
            answers = Questionnaire.model_validate_json(args.answers.read_text())
        elif args.no_input or not sys.stdin.isatty():
            answers = Questionnaire()
        else:
            answers = ask_questionnaire()
        if (llm := _llm()) is None:
            return 1
        brain = onboard(args.url, answers, llm, workspace=args.workspace)
        print_brain(brain)
        print(f"\nSaved to workspaces/{brain.workspace}/brain/v{brain.version:04d}.json")
    elif args.command == "research":
        from growthcrew import config
        from growthcrew.agents.research import ResearchAgent, ResearchInput

        brain = load_brain(args.workspace)
        if (llm := _llm()) is None:
            return 1
        if not config.SEARCH_API_KEY:
            print("Note: SEARCH_API_KEY is not set, so the agent can only read URLs it knows.")
        agent = ResearchAgent(
            llm, max_tool_calls=args.max_tool_calls or config.RESEARCH_MAX_TOOL_CALLS
        )
        report = agent.run(ResearchInput(brand=brain, focus=args.focus))
        print(report.brief.to_markdown(brain.business.name or brain.workspace))
        print(f"\nFull report saved under workspaces/{report.workspace}/research/")
    elif args.command == "strategy":
        from growthcrew.agents.research import load_latest_research
        from growthcrew.agents.strategist import WORKSPACES_DIR, StrategistAgent, StrategyInput
        from growthcrew.reports.strategy import save_strategy

        brain = load_brain(args.workspace)
        research = load_latest_research(args.workspace)
        if (llm := _llm()) is None:
            return 1
        doc = StrategistAgent(llm).run(StrategyInput(brand=brain, research=research))
        report = save_strategy(doc, WORKSPACES_DIR, pdf=not args.no_pdf)
        print(report.read_text())
        print(f"Saved to {report}" + ("" if args.no_pdf else " (and .pdf)"))
    elif args.command == "content":
        from growthcrew.agents.content import WORKSPACES_DIR, ContentAgent
        from growthcrew.agents.strategist import load_latest_strategy
        from growthcrew.reports.content import batch_summary, piece_history, save_batch

        brain = load_brain(args.workspace)
        strategy = load_latest_strategy(args.workspace)
        if (llm := _llm()) is None:
            return 1
        batch = ContentAgent(llm).run_batch(brain, strategy, args.weeks, args.max_items)
        folder = save_batch(batch, WORKSPACES_DIR)
        print(batch_summary(batch))
        # Show the piece the critic changed most.
        print(piece_history(max(batch.pieces, key=lambda piece: len(piece.versions))))
        print(f"Saved to {folder}")
    elif args.command == "cycle":
        from growthcrew.agents.orchestrator import Orchestrator, render_timeline, timeline

        load_brain(args.workspace)
        if (llm := _llm()) is None:
            return 1
        orchestrator = Orchestrator(llm, max_items=args.max_items)
        cycle = orchestrator.run(orchestrator.start_cycle(args.workspace).id)
        print(render_timeline(timeline(orchestrator.engine, cycle.id)))
        print("\nApprove, edit or reject drafts through the API: POST /drafts/<id>/decision")
    elif args.command == "monitor":
        from growthcrew.monitor.run import run_monitors

        load_brain(args.workspace)
        if (llm := _llm()) is None:
            return 1
        result = run_monitors(llm, llm.engine, args.workspace, Path("workspaces"))
        found = ", ".join(f"{name} {count}" for name, count in result.found.items()) or "nothing"
        print(f"Found: {found}. New signals: {result.stored}; duplicates skipped: "
              f"{result.duplicates}.")  # fmt: skip
        for failure in result.failures:
            print(f"Failed: {failure}")
        print(f"Digest: {result.digest_path}")
    elif args.command == "creative":
        from sqlmodel import Session

        from growthcrew.creative.agent import CreativeAgent
        from growthcrew.creative.images import generator
        from growthcrew.creative.render import renderer
        from growthcrew.db.models import Draft

        brand = load_brain(args.workspace)
        if (llm := _llm()) is None:
            return 1
        with Session(llm.engine) as session:
            draft = session.get(Draft, args.draft)
        if draft is None or draft.workspace != args.workspace:
            print(f"No draft {args.draft} in {args.workspace}")
            return 1
        with renderer() as browser:
            agent = CreativeAgent(llm, llm.engine, browser, Path("workspaces"), generator())
            result = agent.run(draft, brand)
        for item in result.rounds:
            lowest = min((min(row.values()) for row in item.scores.values()), default=0)
            print(f"Round {item.round}: {'passed' if item.passed else 'not passed'}, "
                  f"lowest score {lowest}{'; blocked' if item.blocked else ''}")  # fmt: skip
        print(f"Images: workspaces/{args.workspace}/creative/{draft.id}/")
    elif args.command == "landing":
        from growthcrew.creative.landing import export_variants
        from growthcrew.db.session import get_engine

        brand = load_brain(args.workspace)
        for path in export_variants(get_engine(), args.draft, brand, Path("workspaces")):
            print(path)
    elif args.command == "mcp":
        from growthcrew.db.session import get_engine
        from growthcrew.mcp_server import TOKEN_TTL, build_server, mint_approval

        if args.action == "approve":
            if not args.workspace:
                print("--workspace is required")
                return 1
            print(mint_approval(args.workspace, args.allow, args.user))
            print(f"Valid once, for {TOKEN_TTL // 60} minutes, for {args.allow} in "
                  f"{args.workspace}.", file=sys.stderr)  # fmt: skip
            return 0

        def make_llm():
            from growthcrew.llm import LLM

            return LLM(engine=get_engine())

        build_server(get_engine(), args.user, llm_factory=make_llm).run("stdio")
    elif args.command == "connect":
        import os

        from growthcrew.connectors import store as connectors
        from growthcrew.db.session import get_engine

        settings = dict(item.split("=", 1) for item in args.set)
        secret = None
        if args.key_env:
            if not os.getenv(args.key_env):
                print(f"{args.key_env} is not set")
                return 1
            secret = {"api_key": os.environ[args.key_env]}
        elif args.provider == "mcp":
            secret = {}
        connectors.save(get_engine(), args.workspace, args.provider, "cli", secret=secret,
                        settings=settings or None)  # fmt: skip
        print(f"Saved {args.provider} for {args.workspace}. The key is stored encrypted.")
    elif args.command == "sync":
        from growthcrew.connectors.sync import sync_workspace
        from growthcrew.db.session import get_engine

        for run in sync_workspace(get_engine(), args.workspace, Path("workspaces")):
            print(f"{run.provider}: {run.status}, {run.rows} rows, {run.matched} matched "
                  f"{run.error}".rstrip())  # fmt: skip
    elif args.command == "scheduler":
        import time

        from growthcrew.db.session import get_engine
        from growthcrew.scheduler import tick

        analyst = None
        if args.analyse:
            from growthcrew.agents.analyst import AnalystAgent

            if (llm := _llm()) is None:
                return 1
            analyst = AnalystAgent(llm)
        while True:
            for line in tick(get_engine(), Path("workspaces"), analyst=analyst) or ["nothing due"]:
                print(line, flush=True)
            if not args.loop:
                break
            time.sleep(3600)
    elif args.command == "user":
        import getpass

        from growthcrew.api.auth import create_user
        from growthcrew.db.session import get_engine

        try:
            user = create_user(
                get_engine(), args.email.strip().lower(), getpass.getpass(), args.workspaces
            )
        except ValueError as exc:
            print(exc)
            return 1
        print(f"Added {user.email} with access to: {user.workspaces or '(none yet)'}")
    elif args.command == "pilot":
        from datetime import date

        from growthcrew import pilot
        from growthcrew.db.session import get_engine

        try:
            if args.action == "init":
                if not args.start:
                    print("init needs --start YYYY-MM-DD")
                    return 1
                folder = pilot.init_pilot(
                    args.workspace,
                    args.business or args.workspace,
                    date.fromisoformat(args.start),
                    args.owner,
                )
                print(f"Pilot kit written to {folder}/. Start with baseline.csv and plan.md.")
            elif args.action == "track":
                print(pilot.build_tracker(get_engine(), args.workspace))
            elif args.action == "report":
                if not args.day:
                    print("report needs --day 30 or --day 60")
                    return 1
                print(pilot.build_report(get_engine(), args.workspace, args.day))
            else:
                if not (args.quote_file and args.attribution):
                    print("testimonial needs --quote-file and --attribution")
                    return 1
                info = pilot.load_pilot(args.workspace)
                path = pilot.record_testimonial(
                    args.workspace,
                    pilot.Testimonial(
                        quote=args.quote_file.read_text(),
                        name=args.name,
                        role=args.role,
                        business=info.business,
                        attribution=args.attribution,
                        allowed_uses=[use.strip() for use in args.allow.split(",") if use.strip()],
                        wording_confirmed_by_owner=args.confirmed,
                        permission_given_on=date.today(),
                    ),
                )
                print(f"Saved to {path}")
        except ValueError as exc:
            print(exc)
            return 1
    elif args.command == "show":
        print_brain(load_brain(args.workspace, args.version))
    elif args.command == "confirm":
        brain = confirm_fields(args.workspace, args.fields)
        print(f"Saved v{brain.version} with confirmed: {', '.join(args.fields)}")
    return 0


def run() -> int:
    try:
        return main()
    except FileNotFoundError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
