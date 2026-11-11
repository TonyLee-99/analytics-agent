# PRD: governed data and knowledge copilot (v2)

**Problem.** Department analysts in a city 311 operation spend hours a week answering "what does the city
say about X" and "what are the numbers for Y" by hand: searching chicago.gov pages and data-portal
metadata, then writing SQL against the 311 warehouse. A generic chatbot cannot be used because the
documents and the rows are not all visible to everyone, and two departments define "time to close" and
"SLA attainment" differently. A wrong or over-shared number in a council briefing is worse than none.

**Users.** Department analysts (CDOT, Streets and Sanitation, Water, Buildings, Health, 311), the
city-wide performance office, budget analysts. Deployment demonstrated here: **Simulated** org on real
City of Chicago data and documents.

**What it does.** Answers in Slack (also HTTP and MCP) from documents and the warehouse, with citations
(doc id + character span), governed metrics, and an explicit question back when a metric has two
governed definitions. It never shows a document or a row the asker may not see, including right after
a permission change, and does not post restricted answers to channel members who lack access.

**Success metrics (pre-registered in docs/ACCEPTANCE.md).**
- 0 permission leaks after fixes on the red team (half written blind by another model); first-round
  rate reported.
- 0 leaks inside the revocation-lag window with the query-time recheck.
- Answer correctness >= 70% on the question set (half written blind), router >= 85%, recall@5 >= 0.80.
- Asks on >= 80% of ambiguous metric questions, <= 10% false clarifications.
- Every answer traced (user, DB role, sources, SQL, tokens, cost, latency); feedback captured.

**Non-goals.** Okta/SCIM, multiple connectors, Terraform, writing to any system, real pilot users (a
real pilot would replace the Simulated discovery notes).

**Acceptance.** docs/ACCEPTANCE.md (criteria fixed before building, PASS/FAIL with evidence at the end).
