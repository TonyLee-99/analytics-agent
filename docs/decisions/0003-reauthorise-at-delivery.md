# 0003. Re-authorise at delivery time and on every cache hit; reject bad citations

**Decision.** An answer records its dependencies (document versions, departments). Before it is sent,
and whenever it is served from the answer cache, the live systems must still grant all of them;
otherwise it is discarded. An answer citing a source that was not in its context is regenerated once,
then refused, never "fixed" by deleting the citation.

**Rejected.** Event-driven cache invalidation only: events can be lost or late (a test drops them and the
read-path check still evicts). Stripping invalid citations: keeps the unsupported claim.

**Measured reason.** tests/v2/test_caches.py and test_trace.py (revocation during generation, lost
events, policy-version change); red-team cache-after-revocation scenarios.
