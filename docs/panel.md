# The synthetic audience panel

Before a test launches, a panel of synthetic personas reacts to its variants. This page says
what the panel is for, how it is kept honest, and what is known to be wrong with asking a
language model to play a customer.

## What it does

1. **Personas** (`panel/personas.py`). 20 to 50 personas (24 by default,
   `GROWTHCREW_PANEL_SIZE`) are written from the brand brain and the latest research. Each
   lists the evidence IDs it rests on (`brain:icp.pains`, `voc:2`, `claim:7`); unknown IDs
   are removed and a persona with none is dropped. The phrases a persona "uses" must be
   copied from real customer quotes in the research, or they are removed. The panel is
   rewritten when newer research arrives; older generations are kept.
2. **Pre-test** (`panel/pretest.py`). Each persona sees every variant, text and (when it
   exists) the ad image, labelled A, B, C in an order shuffled per persona. It answers: how
   likely it is to stop scrolling (1 to 7), whether it would click, its main objection, and
   anything confusing. An answer that skips or invents an option is not counted.
3. **Aggregation, in code.** Click share ranks the variants, the mean stop score breaks
   ties. Resampling the personas 2,000 times gives each variant's chance of ranking first and
   last. Disagreement is the share of personas whose own favourite was not the panel's pick.
4. **Calibration** (`panel/calibration.py`). When the real test reaches its final verdict
   (judged once at its planned sample), the panel's ranking is compared with the real one:
   Spearman rank correlation and whether the top pick was right, against chance. The Results
   page plots every comparison.

## The rules of use

- **It never picks a winner.** The real, pre-registered test does.
- **It can only suggest removing a clearly weak variant**, and only from a test of three or
  more, so a real comparison always remains. "Clearly weak" means last in at least 95% of
  resamples while the panel is untested, 90% once its record is good. A person decides by
  rejecting that variant's draft; the panel changes nothing itself.
- **Trust is earned and lost on the record.** Fewer than 3 compared tests: "not yet
  checked", shown as a hunch. An average rank correlation under 0.2, or a top-pick hit rate no
  better than chance: "down-weighted", and it stops making suggestions. The app says which.

## Known biases of synthetic respondents

These are documented in the research on using language models as survey respondents, and
they are why the panel is calibrated rather than trusted.

- **Too agreeable.** Models are more positive and more willing to click than real people,
  who ignore most marketing. Absolute numbers (a "40% would click") mean nothing; at most the
  ranking does.
- **Too similar.** Personas written by one model share its tastes. Real audiences disagree
  more; the panel's disagreement figure understates the real spread.
- **Fluent copy wins.** Models reward polished, articulate copy and coherent arguments. Real
  feeds reward novelty, faces, offers and timing, which a text-only reading underweights.
- **Position and label effects.** Models favour options by position or letter. Shuffling the
  order per persona reduces this; it does not remove it.
- **No real stakes, no real context.** A persona is not tired, busy, or scrolling past fifty
  other posts, and does not pay. Stated intent is a poor proxy for behaviour even in humans.
- **Stereotypes.** Demographic personas can reproduce stereotypes about the groups they
  describe. Personas are tied to evidence to limit this, not to remove it.
- **Training-data leakage.** A model may "know" a well-known brand or campaign and react to
  that, not to the message.
- **Images.** Reactions to images rest on the model's vision, which sees a static picture,
  not the ad in motion inside a feed.

If the accuracy chart stays flat or negative, believe the chart: switch the panel off by
ignoring it, and spend the budget on the real test.
