# Feedback to product / platform (learned while building the deployment)

1. **psycopg runs multi-statement strings when no parameters are passed** (simple protocol). Any
   "execute exactly one statement" guarantee must force the extended protocol (`binary=True` or
   `prepare=True`). Worth a lint rule in the platform's DB client wrapper. (INCIDENTS I-1)
2. **The org needs a single owner per metric family.** The copilot can detect that "time to close" has
   two governed definitions and ask, but asking forever is a tax; the product should let a metric
   owner set a default per audience, with the alternative shown as a footnote.
3. **Directory sync lag must be a published SLO.** Query-time rechecks close the window for access,
   but recall silently drops while sync is behind. Expose sync lag next to answer quality.
4. **Channel audience is a product decision, not a backend one.** Ephemeral answers in channels are
   safe but confuse people ("I can see it, why can't my manager?"). The UI should say why an answer was
   private and offer "share to people who can see it".
5. **Data-portal metadata is uneven.** Many performance-metric datasets were last updated 2012–2016 and
   still state targets; answers should surface the "rows last updated" date with any target they quote.

## From the first real user (2026-11-11, the owner, live Slack)
6. **DMs are not channels.** Threading every reply is right in a busy channel and wrong in a DM, where a
   threaded reply looks like no reply. Delivery rules differ per surface and need their own tests.
7. **Model Markdown is not Slack mrkdwn.** Every chat surface needs a formatting adapter at the delivery layer;
   the model's output format should not be trusted to match the surface.
8. **Prompt scaffolding leaks into answers.** Section names in the prompt ("SOURCES", "DATA") showed up in 57 of 135
   recorded eval answers, and nobody noticed because the judge and the tests only checked facts. An answer output
   contract (what may never appear in user-facing text) deserves a mechanical check, like citations do.
