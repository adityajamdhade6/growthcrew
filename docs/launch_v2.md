# Launch drafts (v2)

Drafts for the owner to edit and post. Nothing here is posted automatically. Only measured
figures are used; pilot results and cost per piece are not yet measured.

## Short post

I built GrowthCrew: a five-agent marketing team for small businesses where a person approves
every output. The model writes; code decides what is true. 39 of 39 red-team attacks blocked,
experiments judged once against a pre-registered rule, approve from Slack or email.
Not yet tried with a real business; that pilot is next.

## Longer post

Most AI marketing tools generate copy. GrowthCrew researches with citations, sets a strategy
backed by evidence IDs, writes and edits drafts against the brand's real proof, runs
pre-registered A/B tests, and learns from results, stopping for a human at every approval.
v2 adds always-on competitor and SEO monitoring, visual creative with accessibility checks,
an MCP server, Postgres with a job queue and audit log, and Slack/email approvals.
Measured: 255 tests, 91% coverage, 39/39 red-team attacks blocked, 4.4% worst-case false-winner
rate in simulation. Not measured yet: live-model quality, cost per piece, pilot results.

## Resume bullets

- Built a multi-agent marketing system (Python, FastAPI, Postgres/pgvector, Next.js) with five
  typed agents and a human approval gate; 255 tests at 91% coverage.
- Blocked 39 of 39 red-team attacks (prompt injection, invented proof, auto-publish) by
  checking model output in code against sources and a proof whitelist.
- Designed a Bayesian experiment engine with pre-registration that keeps false winners at
  4.4% worst case across 10,000 simulated null tests.
- Built a durable job queue with tenant isolation and a hash-chained audit log; a 20-workspace
  load test completed with 0 dead jobs and 0 cross-tenant writes.
- Pilot outcome: [X] (not yet measured).

## Interview script (5 minutes)

1. **Problem (30 s)**: small businesses need evidence-based marketing that never posts unseen.
2. **Architecture (90 s)**: five agents, a weekly state machine stopping at approval; one LLM
   wrapper that logs cost and enforces budgets and the kill switch.
3. **The key idea (60 s)**: model writes, code decides; walk through the proof check and the
   red team.
4. **Hard trade-off (60 s)**: one-look experiments are slow for low traffic; sequential
   stopping is on the roadmap instead of loosening the rule.
5. **Honest status (30 s)**: what is measured, what is not, and the pilot plan.
