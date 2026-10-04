# GrowthCrew

A multi-agent AI marketing team for small businesses.

## Goal

Agents that research, strategize, create, and learn, with a human approving every output before it goes live.

## Architecture

Each agent has a clear role, typed inputs and outputs (pydantic), and only the tools it needs.

- `src/growthcrew/agents/`: `research`, `strategist`, `content`, `critic`, `analyst`, and the `orchestrator`, which runs the weekly cycle as a state machine and stops at `awaiting_approval`. The research agent, strategist and content agent run multi-turn `Conversation`s; the critic and analyst make single structured calls.
- `src/growthcrew/llm.py`: the only module that calls the Anthropic API. Agents go through `LLM.call`.
- `src/growthcrew/workflow.py`: the human actions (decide, publish, record metrics). The only code that moves a cycle past `awaiting_approval`.
- `src/growthcrew/budget.py`: weekly spend cap per workspace, checked before every LLM request and between cycle steps.
- `src/growthcrew/integrations/`: exports (calendar CSV, `.eml` drafts) and the publisher registry, which is empty until a real integration is added.
- `src/growthcrew/guardrails.py`: pattern rules that block invented proof, competitor defamation, health and finance claims, and brand-banned language. Runs in the content loop and again on human edits; every block is logged to `GuardrailBlock`.
- `src/growthcrew/pilot.py`: the 60-day pilot kit (baseline, plan, weekly log, tracker, day-30/60 reports, testimonial). Templates and arithmetic only.
- `src/growthcrew/config.py`: model, effort and pricing per agent role. Change models here only.
- `src/growthcrew/brain/`: the brand brain (`models.py`), onboarding from a website (`onboarding.py`), voice extraction (`voice.py`), and versioned storage in `workspaces/<brand>/brain/vNNNN.json` plus the `BrainVersion` table (`store.py`).
- `src/growthcrew/tools/`: `fetch.py` (the polite fetcher every network read goes through), `search.py`, `scrape.py`, `crawl.py`, `reviews.py`, `cache.py`, analytics connectors.
- `src/growthcrew/frameworks/`: one module per marketing framework. Each is a `Framework` (purpose, inputs, pydantic output schema, quality criteria, instructions) rendered through `templates/framework.j2`. Add a framework by adding a module and listing it in `FRAMEWORKS`.
- `src/growthcrew/content/`: content types (`types.py`), one template per type with best practices and platform limits (`templates.py`), and deterministic draft checks (`checks.py`).
- `src/growthcrew/analytics/`: CSV ingest and piece matching (`ingest.py`), significance tests (`stats.py`), and the weekly numbers (`analysis.py`). No model calls in this package.
- `src/growthcrew/reports/`: Markdown and PDF rendering of agent outputs.
- `src/growthcrew/db/`: SQLModel models and migrations.
- `evals/`: the regression suite. `golden/` (3 fictional brands x 10 requests), `calibration/` (20 pieces plus human scores), `suites.py` (one function per eval), `judge.py` (fixed rubrics), `run.py` (scorecard).
- `web/`: the Next.js + Tailwind app. Client components fetch through `lib/api.ts` (`/api` is proxied to FastAPI). Screens live in `app/(app)/`; shared pieces in `components/`.
- `src/growthcrew/api/`: `main.py` (workflow routes), `ui.py` (routes the web app needs), `auth.py` (login, tokens, workspace access), `deps.py`.
- `docs/`: architecture (agents, data flow, storage), case study, launch copy, self-review, and the README's demo GIF.
- `src/growthcrew/naming.py`: the one place that turns ids and enum values into words people read.
- `tests/`, `workspaces/` (one folder per client brand, git-ignored).

## Rules

- Every agent output is structured JSON validated by pydantic. No free-text parsing.
- Every claim about a market or competitor must cite a source URL. Use `schemas.Claim`.
- Nothing is ever published automatically. Outputs are created as `pending_approval` and only a human action changes that. Any new code path that schedules, exports or publishes a draft must call `workflow.require_approved` first, and publishing must go through `workflow.publish` (named human, `confirm=true`).
- Agents never write to `Approval` or change `Draft.status`. Only `workflow.decide` does, on behalf of a named reviewer.
- Every brain field is `inferred` until a human confirms it. Agents receive the brain through `brain/context.py`, which tags each field; never present an inferred field as fact in customer-facing copy.
- Proof (case studies, testimonials, stats) must be real: each item carries a verbatim quote and the URL it came from, and unverifiable items are dropped.
- Brain versions are append-only. Change a brain by saving a new version, never by editing a file.
- Every strategy recommendation has a `support` list of evidence IDs (`brain:<field>`, `learning:N`, `claim:N`, `voc:N`). Unknown IDs are stripped in code and unsupported recommendations are listed under `issues` on the StrategyDoc.
- Scores and totals (ICE, experiment ranking, budget split, quote frequency) are computed or checked in code, not taken from the model.
- Content may use a statistic, customer name or testimonial only if it is in the brain's proof. `content/checks.py` enforces this, the banned-phrase list and platform length limits; a finding caps the critic's score for that criterion, so the model cannot pass a draft the checks fail.
- A content piece goes through at most 3 critic rounds and passes only when all six scores are 8 or more. Pieces that do not pass are still saved, marked as such, for the human reviewer.
- A change backed by a significant A/B winner is accepted by default; a stated risk makes it a partial shift to confirm next week, never a rejection (`strategist.decide_rulings`). Wins seen only across different pieces are always partial.
- Every experiment result is shown with its uncertainty: probability the leader is best, a 95% interval on the lift, and the sample size. Never show a bare "wins", and never print 100% for a probability.
- User-facing text uses `naming.display`, `piece_name` and `plural`; no raw ids or enum values on screen.
- The analyst interprets statistics; it never computes them. A winner exists only where `analytics.stats.compare` returns `significant`; below the minimum sample the answer is `not_enough_data`. An angle shift without a significant readout behind it is blocked in code and the strategist cannot accept it. Do not lower `MIN_TRIALS` or `ALPHA` to get a result.
- Analytics rows that cannot be matched to a piece are reported as unmatched, never assigned by guess.
- A draft with a guardrail violation is `blocked`: it cannot be approved, only rejected or edited until clean. Do not add a way around this.
- Golden references and calibration scores come from a human. Never fill in `reference`, `approved_by` or `human_score` yourself, and never report a pending or skipped eval as passed.
- When a rubric in `evals/judge.py` changes, bump `RUBRIC_VERSION` and re-run calibration.
- Every API route goes through `api.auth.authorize`: a valid token, and access to the workspace the route names or the row it addresses. A new id-based route must use `draft_id`, `cycle_id` or `item_id` as its path parameter so that check applies. The reviewer and publisher recorded on a decision are the signed-in user, never a name from the request body.
- In the web app, every data view handles loading, empty and error states, and every action surfaces the server's error message. Check new screens at phone width; the review panel must stay usable one-handed.
- Pilot reports are before/after comparisons, not experiments. Keep the "Read this first" and caveats sections in every report, never describe a change as caused by GrowthCrew, and flag counts under 30 as too few to interpret.
- A testimonial is the owner's own words with their wording confirmed and each use permitted. Never draft or reword one, and only read it through `pilot.testimonial_for(workspace, use)`.
- Fetch web pages only through `tools.fetch.Fetcher` or `polite_client()`, which refuse private and local addresses, check robots.txt, rate-limit per host and cache. Review sites that forbid scraping are read from exports in `workspaces/<brand>/reviews/`, never scraped.
- Research claims whose URL the agent did not read, and customer quotes that are not verbatim in their source, are dropped in code. Keep it that way; do not relax it to make output look fuller.
- The README, case study and launch copy state only what has been measured. Keep the status note, and leave pilot results and cost per piece marked as not yet measured until real numbers exist; sample or simulated figures must be labelled as such.
- Log every LLM call with tokens and cost. This happens in `llm.py`; never call the Anthropic SDK from anywhere else.

## Workflow

- Explain the plan in 5 bullets before coding.
- Run the tests after: `uv run ruff check . && uv run pytest`.
- After changing any prompt, framework, template, guardrail or statistic, run `make eval` and fix regressions before finishing. `make eval-live` costs money; ask before running it.

## Commands

- Install: `uv sync`
- Test: `make test` (fails below 85% coverage)
- Everything in Docker: `make up`
- Lint and format: `uv run ruff check . && uv run ruff format .`
- Onboard a client: `uv run growthcrew onboard --url https://example.com` (then `show`, `confirm`)
- Research: `uv run growthcrew research <workspace> [--focus "..."] [--max-tool-calls N]`
- Strategy: `uv run growthcrew strategy <workspace>` (writes `.json`, `.md`, `.pdf` under `workspaces/<brand>/strategy/`)
- Content batch: `uv run growthcrew content <workspace> [--weeks 2] [--max-items 8]`
- Weekly cycle: `uv run growthcrew cycle <workspace>` (or `POST /workspaces/<workspace>/cycles`)
- Evals: `make eval` (offline, free) and `make eval-live [LIMIT=5]` (calls the model). Scorecard in `evals/results/`.
- Cost dashboard: `/costs/dashboard?workspace=<workspace>`
- Simulated week (no API key needed): `uv run python evals/simulate_week.py`
- API: `uv run uvicorn growthcrew.api.main:app --reload`
- Web app: `cd web && npm run dev` (set `GROWTHCREW_API_URL` if the API is not on 127.0.0.1:8000); `npm run build` type-checks it
- Add a web user: `uv run growthcrew user add you@example.com --workspaces acme` (or `*` for admin)
- Pilot: `uv run growthcrew pilot init <workspace> --start YYYY-MM-DD --business "Name"`, then weekly `pilot track <workspace>`, and `pilot report <workspace> --day 30|60`
- Read-only Streamlit demo (sample data, for Streamlit Cloud): `uv run streamlit run streamlit_app.py`
- Demo workspace with sample data: `uv run python -m evals.demo_seed`
