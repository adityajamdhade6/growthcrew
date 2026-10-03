# GrowthCrew: building an AI marketing team that knows what it does not know

_A case study. [Add one line on how you built it and over what period.]_

## The problem

Small businesses do marketing in leftover time. The owner of a bakery, a machine shop or a
two-person software company knows their customers better than any agency would, and has
perhaps three hours a week to act on it. The usual options fit badly. An agency is priced for
a bigger company. A freelancer needs a brief the owner has no time to write. A chat assistant
will produce a LinkedIn post in ten seconds, with no strategy behind it, no memory of what
worked last month, and no way of telling whether the statistic it just wrote is real.

That last point is the one that decided the design. The risk in AI marketing is not bad prose.
It is confident, plausible, unsupported claims going out under a real business's name: an
invented testimonial, a made-up competitor weakness, a "winning" headline chosen from forty
visits.

So I set the goal as a team rather than a writer: agents that research, strategise, create
and learn, with a human approving every output before it goes live, and with the system
structurally unable to do the things that would embarrass its owner.

## System design

GrowthCrew is five agents around a shared knowledge base, driven by a weekly state machine.

**The brain.** Onboarding crawls up to 20 pages of the business's site, respecting robots.txt,
and drafts a structured profile: what they sell, the ideal customer, brand voice, products,
proof and competitors. Every field is marked _inferred_ until the owner confirms it, and
agents receive that tag with the value. Proof is held to a stricter rule: a case study,
testimonial or statistic is kept only if its quote appears word for word on the page it cites.

**The agents.** The researcher runs a budgeted tool loop (search, fetch, reviews) and returns
competitor teardowns, voice-of-customer themes and market signals. The strategist applies five
frameworks in order and produces a strategy document. The writer produces seven content types.
The editor scores each draft on six criteria and returns line-level edits, for up to three
rounds. The analyst reads the results and proposes changes.

**The orchestrator.** One weekly cycle moves through research, strategy check, content plan,
drafting and the editor's gate, then stops at _awaiting approval_. Nothing after that point
happens without a named person: approve, edit or reject; then scheduled; then published by
hand; then measured.

The design principle I kept returning to was this: **the model writes, and code decides what
is allowed through.** Wherever a rule mattered, I did not put it in a prompt and hope.

- Research claims citing a URL the agent never read are deleted in code.
- Customer quotes must be verbatim in their source, and theme frequency is counted from the
  surviving quotes, not estimated.
- Every strategy recommendation carries evidence IDs; unknown IDs are stripped and unsupported
  recommendations are listed for the reviewer.
- A number or testimonial in a draft that is not in the brand's proof caps the editor's
  accuracy score, so the model cannot pass a draft the check fails.
- Guardrails block invented proof, competitor disparagement, health and finance claims, and
  brand-banned words. A blocked draft cannot be approved, only rejected or edited until clean.
- A weekly spending cap is checked before every model request.

One module talks to the model. It retries, validates structured output against a schema, and
logs every call with tokens and cost, tagged with the piece it was for.

## Frameworks encoded

I did not want "write a strategy" as a prompt. Each framework is a module with a purpose, the
inputs it draws on, a typed output schema, and quality criteria that the strategist is shown
and the critic later checks against.

- **Positioning**, after April Dunford: competitive alternatives, unique attributes, value,
  target customers, market category, in that order, because each depends on the last.
- **Jobs to be done**: functional, emotional and social, each written as "When..., I want
  to..., so I can...", and allowed to be empty when the evidence does not support one.
- **Messaging house**: one core message, three pillars, proof points under each. A pillar with
  no real proof shows an empty proof list, which makes the gap visible.
- **Funnel and channel plan**: four stages, at most six channel plays, a budget split that
  code checks sums to 100.
- **Test-and-learn**: five experiments with hypothesis, metric, minimum sample and a decision
  rule agreed in advance. ICE scores are computed and ranked in code.

After the first draft, a second call plays devil's advocate in a fresh context, with the
evidence and the quality criteria but none of the strategist's reasoning. The strategist then
logs what it accepts or rejects, with reasons, and revises once. The critique and the log are
kept in the document.

The feedback loop uses the same pattern. The analyst proposes three changes a week; the
strategist rules on each; accepted angle shifts are applied to the next content plan in code;
and a shift that no significant result supports is blocked before the strategist sees it as
acceptable.

## Pilot results

**There are none yet, and I would rather say so than fill this section.** The system has not
run against the live model or for a real business. What exists is evidence about the parts
that can be tested without either.

_Statistics, on seeded data with known answers:_ across 100 simulated three-variant tests with
a true winner, the analyst's test found it 100 times. Across 100 with no real difference it
named a false winner once. Across 100 with a large apparent gap on 30 trials per arm, it
called a winner zero times.

_A simulated week, end to end:_ I generated a week of exports in which the outcome angle was
set to win. Through CSV ingest and analysis, the system found it in the ad A/B test (1.85%
click-through against 1.09% and 1.20%) and across LinkedIn posts (4.33% against about 2.1%),
and declined to call a landing page test at about 40 sessions per variant where one headline
appeared to convert twice as well. With the analyst's and strategist's replies scripted,
since no live model was available, the code that applies an accepted change moved two of five
LinkedIn posts in the next plan to the winning angle.

_Guardrails:_ 28 of 28 labelled cases correct.

The pilot itself is designed and ready: a 30-day baseline recorded before starting, a weekly
ritual, a tracker, and day-30 and day-60 reports that open by stating they are before/after
comparisons with no control group. [Replace this paragraph with the day-60 table: baseline,
pilot period and change for each of the seven metrics, the owner's hours per week, drafts
approved and edited, and model cost per approved piece.]

## What failed, and how I fixed it

**The guardrail eval failed the first time it ran.** It caught two real bugs: "treats night
sweats and anxiety" slipped past the health-claim rule, and a genuine price was flagged as
invented because the pattern swallowed the full stop after "$189". Both were pattern errors a
labelled test set finds in seconds and a demo never would.

**A comparison pooled things that should not be pooled.** My first "which angle wins" readout
combined LinkedIn posts and paid ads because both report click-through. The numbers looked
authoritative and meant little. Comparisons are now made within one content type, and
comparisons across different pieces are labelled observational and capped at medium confidence.

**The interface contradicted the statistics.** The test correctly said "not enough data", and
the chart beside it drew a tall bar that looked like a win. I changed too-early results to
hatched grey bars and rewrote the note in plain language.

**I had written an ROI claim I could not support.** The cost dashboard compared model cost with
freelancer rates I had assumed, and printed "200x cheaper". I removed it. The dashboard now
compares only against quotes the owner enters.

**The fetcher could be pointed inward.** The research agent fetches URLs a model chooses.
Nothing stopped one resolving to localhost or a cloud metadata address. Requests to private
addresses are now refused, redirects included.

**A test found a crash on partial data.** Listing a brain whose fields were not all annotated
raised an error. Small, but it would have been the first thing a new user hit.

## What I would build next

First, the obvious: run it for real. One live pass through every agent, then a 60-day pilot
with one business, and only then tune prompts against what the evals and the owner's edits
show.

Second, calibrate the judge. The content evals depend on an LLM judge agreeing with human
scores, and that needs twenty scored pieces and thirty reference outputs from a person.

Third, close the two manual gaps: one publishing integration behind the existing explicit
publish step, and analytics APIs in place of CSV uploads.

Fourth, measure what a business owner cares about. Click-through is a proxy. The analysis
should weigh leads and replies wherever the data has them.

The thing I would keep unchanged is the stance. The most useful sentence the system produces
is "not enough data yet", and the most important feature is the approve button.
