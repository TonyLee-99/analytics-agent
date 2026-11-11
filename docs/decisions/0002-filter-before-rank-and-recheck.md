# 0002. ACL filter before ranking, BM25 statistics over the allowed set, query-time recheck

**Decision.** Compute the allowed chunk set first, rank only inside it (BM25 IDF and length statistics
from the allowed set), then recheck the top-k against the live source and directory.

**Rejected.** (a) Post-filtering a global top-k: restricted chunks consume slots (recall loss) and scores
leak corpus statistics of restricted documents. (b) Trusting the index's ACL copy: it is stale by the
sync lag.

**Measured reason.** Without the recheck, 15 of 15 retrieval probes inside a 300 s lag window returned a
document the user had just lost; with it, 0 of 15 (results/v2/lag_window.json). A unit test shows BM25
ranks are unchanged when a restricted document full of the query terms is added.
