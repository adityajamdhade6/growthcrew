# Launch copy

Drafts only. Nothing here has been posted or scheduled.

Every claim below is true of the project as it stands on 4 Oct 2026. None of them is a
business result, because no pilot has run. Slots marked [like this] are for numbers that do not
exist yet; do not fill them with estimates.

## LinkedIn post

Formula: number-first opener, three refusals with their receipts, one dated admission, a
closing question and a P.S. About 1,500 characters. Suggested window: Tuesday to Thursday,
7:30 to 9:00 am. Put the repo link in the first comment, not the body.

> 41 visits. 4 sign-ups for one landing page headline, 2 for the other.
>
> Most dashboards would call that a 2x winner. The system I built refused to.
>
> That was one test in a simulated week I designed to fool it.
>
> I've been building GrowthCrew: an AI marketing team for small businesses. A researcher, a strategist, a writer, an editor and an analyst, with a person approving every draft before anything goes out.
>
> The writing is the easy part. What took the time is what it won't do.
>
> It won't call an A/B test with fewer than about 100 visits per variant. Across 100 simulated tests with no real difference, it named a false winner once.
>
> It won't use a statistic or a testimonial that isn't in the brand's own proof. A draft that tries is blocked and can't be approved until it's fixed.
>
> It won't publish. It drafts, an editor agent scores it, and a human says yes.
>
> Now the uncomfortable part. Everything above comes from tests and simulated data. It has 111 automated tests, and as of this week it has not run a single week for a real business.
>
> That is next: a 60-day pilot, with a baseline recorded first and a report that says plainly what can't be credited to the tool.
>
> I'd rather show a system that knows what it doesn't know than a demo with a made-up ROI number.
>
> If you run marketing for a small business: what would you need to see in week one before you trusted an AI team with your brand voice?
>
> P.S. The pilot report template already has a section called "Why these numbers may not mean what they seem to." I wrote it before I had any numbers.
>
> #marketing #aiagents

No voice profile is saved for you, so this uses neutral voice rules. Running
`linkedin-humanizer --mode profile` on a few of your own posts would let the next draft match
how you write.

## X thread (5 posts)

1. 41 visits. 4 sign-ups for headline A, 2 for headline B. Most dashboards call that a 2x winner. The AI marketing team I built said "not enough data yet". Here's what it refuses to do, and why that's the point. 🧵

2. GrowthCrew is five agents for a small business: researcher, strategist, writer, editor, analyst. A human approves every draft. The model writes. Code decides what gets through.

3. It won't call an A/B test under ~100 visits per variant. In 100 simulated tests with no real difference it named a false winner once. With a real difference it found the winner 100 times out of 100.

4. It won't use a stat or testimonial that isn't in the brand's own proof. That draft is blocked: it can't be approved, only fixed or rejected. And nothing is ever published without a person saying yes.

5. The honest status: 111 tests, simulated data, zero real businesses so far. A 60-day pilot is next, baseline first. I wrote the "why these numbers may mislead" section of the report before having any numbers. Repo in the reply.

## Resume bullets

"Built X that did Y, resulting in Z", using only outcomes that exist today.

- Built a multi-agent marketing system (research, strategy, content, critic, analyst) in Python and Next.js with a human approval gate on every output, resulting in a tested end-to-end weekly workflow covered by 111 automated tests at 95% coverage.
- Built a statistical readout layer (two-proportion z-test with Bonferroni correction and a minimum-sample floor) that decides experiment winners in code, not by model judgement, resulting in 100 of 100 true winners found, a 1% false-winner rate and zero calls on undersized samples across seeded datasets.
- Built an eval and guardrail suite (fixed-rubric LLM judge, labelled guardrail cases, CI regression runner) that blocks invented statistics, competitor disparagement and health or finance claims, resulting in 28 of 28 labelled cases passing and two guardrail bugs caught before release.

Once the pilot has run, replace the first bullet's ending with the real outcome, for example:
"...resulting in [metric] moving from [baseline] to [day-60 value] over a 60-day pilot with
[business type], at [cost] in model spend per approved piece."
