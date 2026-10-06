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
- `src/growthcrew/integrations/`: exports (calendar CSV, `.eml` drafts) and the publisher registry (Brevo newsletters only).
- `src/growthcrew/guardrails.py`: pattern rules that block invented proof, competitor defamation, health and finance claims, and brand-banned language. Runs in the content loop and again on human edits; every block is logged to `GuardrailBlock`.
- `src/growthcrew/pilot.py`: the 60-day pilot kit (baseline, plan, weekly log, tracker, day-30/60 reports, testimonial). Templates and arithmetic only.
- `src/growthcrew/config.py`: model, effort and pricing per agent role. Change models here only.
- `src/growthcrew/brain/`: the brand brain (`models.py`), onboarding from a website (`onboarding.py`), voice extraction (`voice.py`), and versioned storage in `workspaces/<brand>/brain/vNNNN.json` plus the `BrainVersion` table (`store.py`).
- `src/growthcrew/tools/`: `fetch.py` (the polite fetcher every network read goes through), `search.py`, `scrape.py`, `crawl.py`, `reviews.py`, `cache.py`, `untrusted.py` (delimits fetched text for the model and flags injected instructions), analytics connectors.
- `src/growthcrew/frameworks/`: one module per marketing framework. Each is a `Framework` (purpose, inputs, pydantic output schema, quality criteria, instructions) rendered through `templates/framework.j2`. Add a framework by adding a module and listing it in `FRAMEWORKS`.
- `src/growthcrew/content/`: content types (`types.py`), one template per type with best practices and platform limits (`templates.py`), and deterministic draft checks (`checks.py`).
- `src/growthcrew/experiments/`: the experiment engine (Bayesian A/B/n, decision rule, pre-registration, power, bandit, guardrails, simulations). Standalone: it must not import anything else from GrowthCrew, and a test enforces that.
- `src/growthcrew/memory/`: content memory with embeddings (`store.py`, `embed.py`; the default embedder is lexical, and must be described as lexical wherever it is mentioned), the weekly pattern miner and rule decay (`miner.py`), and the playbook the agents read (`playbook.py`).
- `src/growthcrew/creative/`: visual creative (Phase 5): the brand kit (`kit.py`), HTML templates rendered by Chromium in three sizes (`templates/`, `render.py`), accessibility and platform checks (`access.py`), the slot filler and vision critic loop (`agent.py`), optional AI backgrounds (`images.py`), landing page variants (`landing.py`).
- `src/growthcrew/panel/`: the synthetic audience panel (Phase 6): evidence-backed personas (`personas.py`), the pre-test and its aggregation in code (`pretest.py`), scoring against final verdicts (`calibration.py`), and its hook in the weekly cycle (`cycle.py`). Known biases are in `docs/panel.md`.
- `src/growthcrew/monitor/`: the always-on research (Phase 4): competitor page snapshots and diffs plus ad exports (`competitors.py`), Search Console gaps, lexical topic clusters, briefs and ranking moves (`seo.py`), social listening from exports and allowed forums (`social.py`), what to watch (`settings.py`), and the Signals inbox with dedupe, learned ranking and the weekly digest (`signals.py`). `run.py` runs them all; the weekly cycle calls it in the research stage.
- `src/growthcrew/connectors/`: live data (Phase 7): encrypted per-workspace credentials (`store.py`), Google OAuth with Search Console and GA4 (`google.py`), Brevo newsletters and stats (`brevo.py`), HubSpot counts (`hubspot.py`), rows from other MCP servers (`mcp_source.py`), the imports folder (`imports.py`), and `sync.py`. `mcp_server.py` serves GrowthCrew over MCP; `scheduler.py` runs the daily sync and weekly analysis; `keys.py` holds signing and encryption.
- `src/growthcrew/analytics/`: CSV ingest and piece matching (`ingest.py`), registrations and final verdicts (`registry.py`), and the weekly numbers (`analysis.py`). No model calls in this package.
- `src/growthcrew/reports/`: Markdown and PDF rendering of agent outputs.
- `src/growthcrew/db/`: SQLModel models, the session (SQLite or Postgres with pgvector) and Alembic migrations (`db/migrations/versions/`).
- Production backbone (Phase 9): `jobs.py` (durable queue, leases, retries), `tenancy.py` (every query scoped to its workspace), `audit.py` (hash-chained, append-only log), `safety.py` (kill switch, log scrubber), `observability.py` (Sentry), roles in `api/auth.py`. See `docs/operations.md`.
- `evals/`: the regression suite. `golden/` (3 fictional brands x 25 requests, hard cases marked), `calibration/` (20 pieces with human scores, 50 pairs with human preferences), `suites.py` (one function per eval), `judge.py` (fixed rubrics and the position-swapped pairwise judge), `redteam.py` (attacks that must fail), `gate.py` (the CI gate against `baseline.json`), `routing.py` (model-routing experiment), `run.py` (scorecard).
- `src/growthcrew/tracing.py` (one trace per cycle, OTLP export) and `budgets.py` (per-agent cost and latency limits with alerts).
- `web/`: the Next.js + Tailwind app. Client components fetch through `lib/api.ts` (`/api` is proxied to FastAPI). Screens live in `app/(app)/`; shared pieces in `components/`.
- `src/growthcrew/api/`: `main.py` (workflow routes), `ui.py` (routes the web app needs), `auth.py` (login, tokens, workspace access), `deps.py`.
- `docs/`: architecture (agents, data flow, storage), roadmap (what is planned and why, including sequential stopping), case study, launch copy, self-review, and the README's demo GIF.
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
- A change backed by a called winner is accepted by default; a stated risk makes it a partial shift to confirm next week, never a rejection (`strategist.decide_rulings`).
- Every experiment result is shown with its uncertainty: probability the leader is best, a 95% interval on the lift, and the sample size. Never show a bare "wins", and never print 100% for a probability.
- User-facing text uses `naming.display`, `piece_name` and `plural`; no raw ids or enum values on screen.
- Playbook rules come from comparing pieces that happened to differ, so they are leads, not results. Agents follow only `active` rules; the editor's playbook check is advice and never caps a score; and a rule may shift content only through the normal path (a proposed change backed by a called winner).
- Do not loosen the miner to keep a rule alive: two runs to activate, one miss to take it out of use, and the within-group comparison against the strongest feature all stay.
- Recurring human edits are proposed as voice rules and written into the brain only when a person accepts them.
- The analyst interprets statistics; it never computes them. A winner exists only when a pre-registered test, judged once at its planned sample on its registered metric, meets the decision rule in `experiments/decide.py`. Unregistered comparisons are descriptive only. Do not loosen `loss_threshold`, the probability requirement or `MIN_TRIALS` to get a result, and do not let a verdict be recomputed after it is final.
- A winner that damages a registered guardrail metric is held for a person: the strategist cannot accept it and the bandit does not give it the budget.
- Analytics rows that cannot be matched to a piece are reported as unmatched, never assigned by guess.
- A draft with a guardrail violation is `blocked`: it cannot be approved, only rejected or edited until clean. Do not add a way around this.
- Golden references and calibration scores come from a human. Never fill in `reference`, `approved_by` or `human_score` yourself, and never report a pending or skipped eval as passed.
- Every red-team case must pass; never weaken a case or delete one to get green. Update `evals/baseline.json` only in its own commit that says why, never to hide a regression. A pairwise preference counts only if it survives swapping the order.
- When a rubric in `evals/judge.py` changes, bump `RUBRIC_VERSION` and re-run calibration.
- Every API route goes through `api.auth.authorize`: a valid token, and access to the workspace the route names or the row it addresses. A new id-based route must use `draft_id`, `cycle_id`, `item_id` or `signal_id` as its path parameter so that check applies (add a new one to `auth.ID_PARAMS`). The reviewer and publisher recorded on a decision are the signed-in user, never a name from the request body.
- In the web app, every data view handles loading, empty and error states, and every action surfaces the server's error message. Check new screens at phone width; the review panel must stay usable one-handed.
- Pilot reports are before/after comparisons, not experiments. Keep the "Read this first" and caveats sections in every report, never describe a change as caused by GrowthCrew, and flag counts under 30 as too few to interpret.
- A testimonial is the owner's own words with their wording confirmed and each use permitted. Never draft or reword one, and only read it through `pilot.testimonial_for(workspace, use)`.
- Fetch web pages only through `tools.fetch.Fetcher` or `polite_client()`, which refuse private and local addresses, check robots.txt, rate-limit per host and cache. Review sites that forbid scraping are read from exports in `workspaces/<brand>/reviews/`, never scraped.
- The model fills template slots; it never writes layout HTML or CSS. Words on an image go through `guardrails.check` like any copy. Measured checks (contrast, text size, clipping, safe area, text share, alt text) cap the vision critic's scores; do not let the critic pass an image the checks fail, and keep the 3-round limit.
- Rendering refuses all network requests; brand images are uploaded files checked by signature, never URLs. A generated image is always labelled AI-generated, in its metadata and on screen. Exporting or downloading creative for use needs an approved draft (`workflow.require_approved`).
- The synthetic panel never replaces a real test and never picks a winner. It may only suggest dropping a clearly weak variant from a test of three or more, a person decides, and it suggests nothing while its calibration is low. Score it only against final verdicts; do not lower `MIN_TESTS`, `MIN_CORRELATION` or the drop probabilities to make it look useful. Persona phrases must be verbatim customer quotes, and its numbers are never shown as predictions of real click rates.
- Connector secrets are stored only through `connectors.store` (encrypted) and never returned by the API. Use read-only scopes; refuse wider grants. Connector tests replay recorded fixtures; never call a live API in tests. MCP tools are read-only unless they spend a single-use approval token, and no MCP tool approves, schedules or publishes. Only approved newsletters are ever sent; cold emails are never sent automatically.
- Fetched pages, exports and reviews are untrusted data. Pass them to a model only through `tools.untrusted.wrap`, put `DATA_RULE` in the system prompt, never give a model tools in a call that reads them unless the results are wrapped too, and check the output in code (citations, verbatim quotes, known links). Never let fetched text change a prompt or choose a tool.
- Every monitor finding cites a URL and a date; a finding without one is dropped. A signal reaches the strategy only when a person sends it from the inbox, and the person recorded is the signed-in user.
- Research claims whose URL the agent did not read, and customer quotes that are not verbatim in their source, are dropped in code. Keep it that way; do not relax it to make output look fuller.
- The README, case study and launch copy state only what has been measured. Keep the status note, and leave pilot results and cost per piece marked as not yet measured until real numbers exist; sample or simulated figures must be labelled as such.
- Every model change ships with an Alembic migration (`uv run alembic revision --autogenerate`); a test fails if they disagree. Never add a NOT NULL column without a server default.
- Job handlers must be safe to run twice; long work runs through `jobs.enqueue` with an idempotency key, and model calls inside a resumable unit run in `llm.cache_scope`.
- Never bypass `tenancy`: do not query across workspaces inside a request; cross-workspace work (admin reports, the scheduler) runs outside a tenant scope on purpose.
- The audit log is append-only: record approvals, edits, publishes and access changes with `audit.record`, and never update or delete its rows. Owner-only routes are listed in `auth.OWNER_ONLY`.
- Log every LLM call with tokens and cost. This happens in `llm.py`; never call the Anthropic SDK from anywhere else.

## Workflow

- Explain the plan in 5 bullets before coding.
- Run the tests after: `uv run ruff check . && uv run pytest`.
- After changing any prompt, framework, template, guardrail or statistic, run `make eval` and fix regressions before finishing. `make eval-live` costs money; ask before running it.

## v2 working rules

From the v2 upgrade pack (13 phases). They apply to every phase.

- Start each phase by reading this file and `docs/architecture.md`; update both when the phase ends.
- One git branch per phase. Do not merge pull requests: open the PR, wait for CI, then give the owner a 5-line summary of what changed and which files to read. The owner merges. If a branch has to stack on an unmerged one, say which order to merge in.
- After each phase, ask what a senior engineer would criticise, and fix the top 3.
- The public demo runs on sample data only. Real client data never goes into the demo.

## Commands

- Install: `uv sync`
- Test: `make test` (fails below 85% coverage)
- Everything in Docker: `make up`
- Lint and format: `uv run ruff check . && uv run ruff format .`
- Onboard a client: `uv run growthcrew onboard --url https://example.com` (then `show`, `confirm`)
- Research: `uv run growthcrew research <workspace> [--focus "..."] [--max-tool-calls N]`
- Strategy: `uv run growthcrew strategy <workspace>` (writes `.json`, `.md`, `.pdf` under `workspaces/<brand>/strategy/`)
- Content batch: `uv run growthcrew content <workspace> [--weeks 2] [--max-items 8]`
- Monitors and signals digest: `uv run growthcrew monitor <workspace>` (or `POST /workspaces/<workspace>/monitor`)
- Ad images for a draft: `uv run growthcrew creative <workspace> --draft <id>`; approved landing variants as HTML: `uv run growthcrew landing <workspace> --draft <id>`
- MCP server: `uv run growthcrew mcp serve --user you@example.com`; connect a source: `uv run growthcrew connect <workspace> brevo --key-env BREVO_KEY --set list_id=4`; sync: `uv run growthcrew sync <workspace>`; scheduler: `uv run growthcrew scheduler [--loop] [--analyse]`
- Weekly cycle: `uv run growthcrew cycle <workspace>` (or `POST /workspaces/<workspace>/cycles`)
- Evals: `make eval` (offline, free) and `make eval-live [LIMIT=5]` (calls the model). Scorecard in `evals/results/`.
- CI gate locally: `make eval && make eval-gate`; routing experiment (costs money): `make eval-routing LIMIT=6`; a cycle's trace: `uv run growthcrew trace <cycle> [--otlp file.json]`
- Worker: `uv run growthcrew worker`; migrations: `uv run growthcrew db upgrade`; audit check: `uv run growthcrew db verify-audit`; kill switch: `uv run growthcrew pause on|off --by <name> [--workspace w]`; load test: `uv run python -m evals.loadtest --database-url <postgres>`
- Cost dashboard: `/costs/dashboard?workspace=<workspace>`
- Twelve simulated weeks through the pattern miner: `uv run python -c "from sqlmodel import SQLModel, create_engine; from growthcrew.db import models; from growthcrew.memory.simulate import run; e = create_engine('sqlite://'); SQLModel.metadata.create_all(e); [print(w) for w in run(e)]"`
- Experiment engine simulation report (writes charts to `reports/experiments/`): `uv run python -m evals.experiments_report`
- Simulated week (no API key needed): `uv run python evals/simulate_week.py`
- API: `uv run uvicorn growthcrew.api.main:app --reload`
- Web app: `cd web && npm run dev` (set `GROWTHCREW_API_URL` if the API is not on 127.0.0.1:8000); `npm run build` type-checks it
- Add a web user: `uv run growthcrew user add you@example.com --workspaces acme` (or `*` for admin)
- Pilot: `uv run growthcrew pilot init <workspace> --start YYYY-MM-DD --business "Name"`, then weekly `pilot track <workspace>`, and `pilot report <workspace> --day 30|60`
- Read-only Streamlit demo (sample data, for Streamlit Cloud): `uv run streamlit run streamlit_app.py`
- Demo workspace with sample data: `uv run python -m evals.demo_seed`
