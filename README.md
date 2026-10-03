# GrowthCrew

**An AI marketing team for small businesses, where a human approves every output.**

![Mission control, the content calendar, the review panel and results, shown with sample data](docs/demo.gif)

Five agents research the market, set the strategy, write the content, edit it and read the
results. You approve, edit or reject each draft, usually from your phone. Each week the
analyst proposes changes, the strategist rules on them, and next week's plan shifts.

> **Status, plainly.** The system is built and tested (111 tests, 95% coverage, offline evals
> passing). It has **not yet run against the live model or for a real business**: every
> screenshot here uses a sample brand with invented data. The pilot and cost sections below
> say what will be measured, and are empty until it is.

## The problem

A small business owner does marketing in the gaps between everything else. Agencies are priced
for bigger companies, freelancers need briefing and managing, and a chat assistant produces
plausible copy with no strategy behind it, no memory of what worked, and no check on whether
the "92% of customers" it just wrote is true.

The owner does not need more words. They need a team that knows the business, argues from
evidence, learns from results, and never publishes anything they have not seen.

## How it works

```mermaid
flowchart LR
    W[Website + questionnaire] --> B[(Brand brain<br/>each field inferred or confirmed)]
    B --> R[Researcher<br/>search, read, reviews]
    R -->|cited claims only| S[Strategist<br/>5 frameworks + devil's advocate]
    S --> C[Writer<br/>7 content types]
    C <-->|scores + line edits<br/>max 3 rounds| E[Editor]
    E --> G{Guardrails}
    G -->|blocked| C
    G --> H((You approve,<br/>edit or reject))
    H -->|edits become voice rules| B
    H --> P[Scheduled, then published by you]
    P --> M[Results uploaded]
    M --> A[Analyst<br/>significance tests in code]
    A -->|3 proposed changes| S
    S -->|accepted changes| C
```

**The loop that matters** is the bottom one: results come back, the analyst proposes three
changes, the strategist accepts or rejects each with a reason, and accepted changes alter next
week's content plan. Every ruling is kept in a learning log.

## What it refuses to do

The model writes; code decides what is allowed through.

| It will not | How that is enforced |
|---|---|
| Publish anything | Every draft is created `pending_approval`. One function guards scheduling, export and publishing, and requires a named human's approval. |
| Use an invented statistic or testimonial | A number or quote that is not in the brand's own proof caps the editor's accuracy score and blocks the draft. |
| Make an uncited claim about a competitor or market | Research claims whose URL the agent never read are deleted. Customer quotes must be verbatim. |
| Call a winner on a tiny sample | A two-proportion z-test, Bonferroni-corrected, with a floor of about 100 trials per variant. Below it the answer is "not enough data". |
| Treat a guess as a fact | Every brain field is `inferred` until you confirm it, and agents see that tag. |
| Overspend | A weekly cap per workspace and an optional total cap, checked before every model request. |

## Eval scorecard

From `make eval` (offline; no API key). Full output in `evals/results/scorecard.md`.

| Eval | Result |
|---|---|
| Analyst: finds the true winner | 100 of 100 seeded datasets (3 variants), 100 of 100 (2 variants) |
| Analyst: calls a winner when there is none | 1 of 100 |
| Analyst: calls a winner on a tiny sample | 0 of 100 |
| Analyst: simulated weeks, end to end through CSV ingest | 10 of 10 correct |
| Guardrails: labelled cases | 28 of 28 (every must-block line blocked, no false blocks) |
| Golden set (3 brands x 10 requests) | Built; 0 of 30 human reference outputs written yet |
| Judge calibration against 20 human scores | Pieces ready; 0 of 20 human scores yet |
| Strategy, content and research evals | Written; need the live model (`make eval-live`) |

## Pilot results

**None yet.** A 60-day pilot kit is built (`growthcrew pilot init`): a 30-day baseline, a plan,
a weekly ritual, a tracker, and day-30 and day-60 reports. When a pilot has run, this section
will show, against baseline: followers, impressions, engagement rate, sessions from social,
leads, email reply rate and the owner's hours per week.

The reports are before/after comparisons, not experiments, and say so on the first screen.
Each one lists what else happened in the period and flags any count under 30 as too small to
interpret.

## Cost per piece

**Not measured yet.** Every model call is logged with tokens and cost, and tagged with the
piece it was for, so the dashboard at `/costs/dashboard` will show cost per agent, per piece
and per week once the system has run. It makes no freelancer comparison until you enter quotes
you have actually received.

## Run it

With Docker (not yet tested on a machine with Docker installed):

```bash
docker compose up --build
```

Then open http://localhost:3000 and use the demo login button. Without an `ANTHROPIC_API_KEY`
the app runs on the sample brand and model actions return a clear "no key configured" message.

Without Docker:

```bash
uv sync
uv run python -m evals.demo_seed          # sample brand and a local demo login
uv run uvicorn growthcrew.api.main:app --port 8000
cd web && npm install && npm run dev      # http://localhost:3000
```

To run the agents for real, copy `.env.example` to `.env`, add `ANTHROPIC_API_KEY` (and a
Brave Search key as `SEARCH_API_KEY`), then:

```bash
uv run growthcrew onboard --url https://your-business.com
uv run growthcrew cycle your-business
```

Checks: `make lint`, `make test`, `make eval`. `render.yaml` is a deploy blueprint for a public
demo with a spending cap; it has not been deployed.

## Limitations

- **Unproven in the wild.** No live model run and no real business yet. Prompts that pass with
  a scripted test model may need work with the real one.
- **Publishing is manual.** You copy the approved text, post it, and mark it published. No
  platform integrations yet.
- **Results come from CSV uploads.** No analytics APIs, and export column names were mapped
  from memory, not from real files.
- **Guardrails are pattern rules.** They catch listed phrasings of health, finance and
  defamatory claims, not every rewording.
- **The judge is uncalibrated** until 20 human scores exist, and uses the same model as the
  agents by default.
- **Text only.** No images, video or design for ads and social.
- **One process, SQLite.** Background work runs inside the API process; fine for a pilot, not
  for scale.

## Roadmap

1. Run every agent against the live model; fix what breaks; record real cost per piece.
2. A 60-day pilot with one real business, baseline first.
3. Human reference outputs and judge calibration, so content evals mean something.
4. One real publishing integration behind the existing explicit-publish step.
5. Analytics APIs in place of CSV uploads.
6. A job queue and Postgres.

## Repo map

| Path | What is there |
|---|---|
| `src/growthcrew/agents/` | Researcher, strategist, writer, editor, analyst, orchestrator |
| `src/growthcrew/brain/` | Brand knowledge base, onboarding, voice learning |
| `src/growthcrew/frameworks/` | Positioning, jobs-to-be-done, messaging house, funnel, test-and-learn |
| `src/growthcrew/analytics/` | CSV ingest and the statistics |
| `src/growthcrew/guardrails.py`, `workflow.py`, `budget.py` | What is blocked, who approves, what it may spend |
| `evals/` | Golden set, judge, eval suites, scorecard runner |
| `web/` | Next.js app: onboarding, mission control, strategy, calendar, results, settings |
| `docs/` | [Case study](docs/case_study.md), [launch copy](docs/launch.md), [self-review](docs/review.md) |
