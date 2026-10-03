# Self-review: the ten biggest weaknesses

Reviewed twice: as a senior AI engineer would read the repo, and as a VP of Marketing would
read the pitch. Ordered by how much each would cost in a hiring conversation or a pilot.

## As a senior AI engineer

1. **It has never called the live model.** Every agent test uses a scripted client. Schemas
   the API rejects, prompts that underperform and real token costs are all unknown.
   _Not fixable without an API key. First item on the roadmap._
2. **Model-chosen URLs could reach the server's own network.** The research agent fetches
   URLs the model picks, and onboarding fetches one a user types.
   **Fixed:** requests to private, loopback and link-local addresses are refused, on redirects
   too (`tools/fetch.py`).
3. **A restart mid-cycle left work "running" forever.** Background work lives in the API
   process, so a deploy or crash orphaned the step.
   **Fixed:** on startup, interrupted steps are marked failed and the cycle is left resumable
   (`recover_interrupted`).
4. **No migrations.** Adding a column broke any existing database.
   **Fixed for the common case:** startup now adds missing tables and columns. Renames and
   type changes still need a real migration tool.
5. **Sign-in could be brute-forced.** No limit on password attempts.
   **Fixed:** five wrong passwords lock that email for 15 minutes. Still open: the token is
   kept in browser storage, and the lockout is per process.
6. **The eval judge is uncalibrated and not independent.** It defaults to the same model as
   the agents, and there are no human scores yet to compare it with.
7. **One process and SQLite.** No job queue; a long cycle ties up a worker.

## As a VP of Marketing

8. **No results.** No pilot, no real brand, no before and after. Everything shown is sample
   data. _Not fixable at a desk; the pilot kit exists so this can be done properly._
9. **It claimed an ROI it could not support.** The cost dashboard compared model cost with
   freelancer rates I had assumed and printed "200x cheaper".
   **Fixed:** the multiplier is gone, and no comparison is shown until the owner enters quotes
   they have actually received.
10. **Success is measured in clicks.** Click-through is the primary metric for most channels,
    which rewards curiosity, not customers. Leads and replies are used only where an export
    happens to carry them.

Also noted, not in the top ten: publishing and analytics are manual; voice rules learned from
edits apply without a confirmation step; content is text only.
