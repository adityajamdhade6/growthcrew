# Case study: GrowthCrew v2

> Measured figures only. Pilot results and cost per piece are **not yet measured**; the
> load test and MixLab demo use simulated or synthetic data and are labelled as such.

## The problem

Small businesses need marketing that is grounded in evidence, learns from results and never
publishes anything a person has not seen. A chat assistant gives fluent copy with none of that.

## What was built

A five-agent team (research, strategy, writing, editing, analysis) run as a weekly state
machine that stops at human approval. v2 added always-on monitoring, visual creative with a
measured-accessibility critic, a synthetic audience panel, live data connectors and an MCP
server, a production backbone (Postgres, job queue, tenancy, audit log), approval from Slack
and email, and a connection to the MixLab marketing-mix model.

## Design decisions

1. **The model writes; code decides.** Proof, citations, scores, statistics and budget splits
   are checked or computed in code. A finding caps the critic's score, so the model cannot
   argue a draft past a failed check.
2. **Untrusted text is data.** Fetched pages are wrapped and delimited, and model output is
   checked against them (citations read, quotes verbatim, links known).
3. **Statistics are pre-registered.** One look at the planned sample, a Bayesian decision rule,
   and every result shown with probability, a 95% interval and sample size.
4. **Approval cannot be bypassed.** One function guards every export and publish; Slack and
   email approvals go through the same path, need the approver role and are audited.

## Measured so far

| What | Result | Source |
|---|---|---|
| Tests | 255 passing, 91% line coverage | `make test` |
| Red-team attacks | 39 of 39 fail as they should | `make eval` |
| False winners between identical variants | 4.4% worst case over 5 scenarios x 2,000 tests | `make eval` |
| Guardrail labelled cases | 28 of 28 | `make eval` |
| Load test, Postgres, simulated model | 20 cycles in 13.8 s on 6 workers, 0 dead jobs, 0 cross-tenant drafts | `evals/loadtest.py` |

## Not yet measured

Live-model quality, real cost per piece, pilot outcomes, usability test results, judge and
panel calibration. Each has a harness ready; none has numbers yet.

## What I would do next

Run the live model on the golden set, collect the 75 human references and 20 calibration
scores, then run a real 60-day pilot.
