# Discovery notes (**Simulated**)

These notes are **Simulated**: no real City of Chicago employee was interviewed. They are written the way
a deployment engineer would capture a first discovery call, to make the scope decisions explicit. The
persona is invented; the data and documents referenced are real.

## Who: Jules Romero (Simulated), 311 analytics lead
Runs a 4-person analytics team that answers ad-hoc questions from department managers and aldermanic
offices; owns the weekly 311 performance deck.

## Pains (as told)
1. "Half my week is the same five questions: backlog by department, how fast potholes get fixed,
   whether rodent complaints are up, and 'what's our policy on X'."
2. "Water and Budget won't let us send their internal pages around. If a bot shows those to CDOT, the
   project dies that day." -> permissions are the launch blocker, not accuracy.
3. "CDOT reports time-to-close on completed requests; Streets and San counts cancellations too. Both
   are 'right'. Last quarter the deck had both numbers on two slides." -> conflicting definitions.
4. "People get moved between teams constantly; offboarding takes days to reach every system." ->
   revocation lag is normal, not an edge case.
5. "Everything happens in Slack channels with mixed teams." -> audience-aware delivery.

## Current process
Analyst searches chicago.gov and the data portal, writes SQL in a shared notebook as a service account
(everyone sees everything), pastes numbers into Slack. No record of which definition was used.

## Constraints
No new SaaS without security review; identity comes from the city directory (Slack email must match);
data stays on city infrastructure; answers must show sources.

## How scope changed after discovery
- Added: query-time recheck and delivery-time reauthorisation (pain 4), channel audience check (pain 5),
  metric-family conflict detection that asks instead of choosing (pain 3).
- Moved down: dense retrieval tuning and rerank (nice to have; measured, off by default).
- Dropped: write actions (create/close requests) — read path first.
