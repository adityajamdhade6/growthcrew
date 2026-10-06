# Roadmap

What is planned and not yet built, with the reason each matters. Phases refer to the v2
upgrade pack.

## Experiments

### Sequential stopping (not built)

Today the experiment engine looks once: a test is judged when every variant reaches its
planned sample, and that verdict is final. It cannot stop early, however lopsided the result.

That is the right trade for high-traffic tests, where the planned sample arrives in a week or
two. It is costly for **low-traffic B2B tests**. A contract manufacturer might get a few
hundred LinkedIn impressions a post and a handful of RFQs a month, so a planned sample can
take a quarter to fill, and a clear winner goes unused for most of it.

A sequential design would allow a decision at planned interim looks while keeping the
false-winner rate under 5% across all of them, either with an alpha-spending boundary
(O'Brien-Fleming style: very strict early, close to the fixed-sample threshold at the end) or
with a Bayesian rule whose thresholds are calibrated by simulation for the number of looks.

What it needs before it ships:

- The number and timing of looks fixed in the pre-registration, not chosen afterwards.
- A simulation, like the existing one in `reports/experiments/`, showing the false-winner rate
  across all looks stays under 5% and reporting the average sample saved.
- The current rule kept as the default; sequential stopping is opted into per test.

Until then, low-traffic tests should be registered on the highest-volume metric available
(impressions to clicks), with rarer outcomes such as RFQs tracked alongside as outcomes, not
used as the test metric. B2B mode does this.

### Also planned

- Drift detection for the bandit, so a winner that fatigues is noticed.
- Guardrails compared against every other variant, not only the control.
- Revenue per visitor fed by real order data (the model exists; nothing supplies it yet).

## Memory

### A real embedding model (Phase 9)

Content memory currently uses a **lexical** embedder: it hashes words and character n-grams,
so it matches pieces that share wording and topic vocabulary. It does not understand meaning:
"cheap" and "affordable" are unrelated to it.

Phase 9 moves storage to Postgres with pgvector and replaces the default with a model-backed
embedder behind the existing `Embedder` interface. Work involved: choose the model, re-embed
existing memory, store vectors in a pgvector column with an index, and compare retrieval
quality against the lexical baseline on the golden set before switching the default.

## Still open from v1

- Run every agent against the live model and record real cost per piece.
- A 60-day pilot with a real business, baseline first.
- Human reference outputs and judge calibration.
- One real publishing integration behind the explicit publish step.
- Analytics APIs in place of CSV uploads.
- A job queue and Postgres.
