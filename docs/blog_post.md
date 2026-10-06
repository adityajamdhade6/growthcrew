# Letting the model write, and making code decide

*A technical write-up of GrowthCrew v2. Draft, not yet published.*

Most "AI marketing" tools wrap a prompt around a text box. GrowthCrew starts from the opposite
assumption: the model is a good writer and an unreliable judge of fact, so every claim it makes
must be checkable, and every check lives in code.

## 1. Proof is a whitelist

The writer may only use a statistic, customer name or testimonial that exists in the brand's
proof, each stored with a verbatim quote and its source URL. `content/checks.py` scans drafts
for numbers, ratings and quotes; a finding caps the editor's accuracy score. The red team
includes drafts that slip a "4.9 stars" or "200 happy clients" past a careless prompt. All 39
attacks currently fail.

## 2. Fetched text is data, not instructions

Every page, review export and search result passes through `tools/untrusted.wrap`, and every
system prompt that reads it carries a rule that text inside the markers is never an instruction.
Outputs are then checked: cited URLs must be ones the agent actually read, quotes must be
verbatim in their source.

## 3. Experiments are judged once

Tests are pre-registered with a metric, a sample size and a decision rule. They are judged once
at the planned sample, and the verdict is frozen. In simulation, identical variants produce a
false winner 4.4% of the time at worst. Results always carry the probability the leader is best,
a 95% interval on the lift and the sample size; "100%" is never printed.

## 4. Approval is one function

`workflow.require_approved` guards every export, schedule and publish. Slack buttons and email
links reach the same `workflow.decide`, for a named user with the approver role. An email link
only opens a confirmation page, so a mail scanner that follows links approves nothing.

## 5. The backbone is boring on purpose

Postgres, a job queue with leases and idempotency keys, a per-cycle response cache so a resumed
stage does not pay twice, tenant scoping on every query, and an append-only, hash-chained audit
log. A load test of 20 workspaces with a simulated model finished with no dead jobs and no
draft written under the wrong workspace.

## What is not proven

No live-model run, no real pilot, no real cost per piece yet. Those are the next step, and they
will be reported whatever they show.
