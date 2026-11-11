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
