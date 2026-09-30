---
id: 0004
type: record
title: API contract — bearer client-id, 401 for missing/malformed/unknown, fixed JSON bodies
status: accepted
deciders: Albert-Louis Cohen
date: 2026-09-30
supersedes:
superseded-by:
---

# 0004 — API contract

**Context.** The brief defines requests carrying `Authorization: bearer <client-id>`
and the bodies for 200 and 429. It is inconsistent in two places:
- its requirement text writes `{ succes:true }`, while its curl example shows
  `"success": true`;
- it writes `bearer` in one place and `Bearer` in another.

It says nothing about requests without a valid client.

**Decision.**
- `Authorization: Bearer <client-id>`. The scheme is matched case-insensitively
  (`bearer`, `Bearer`); the client-id is matched exactly.
- **401** `{"error": "unauthorized"}` for a missing header, a malformed header
  (wrong scheme, empty id), or a client-id not in the configuration.
- **200** `{"success": true}`, following the curl example rather than the typo.
- **429** `{"error": "rate limit exceeded"}`.
- Limits are per client and per endpoint, and live in a YAML config file. A client
  on `/foo` and the same client on `/bar` have separate state.

**Rejected.**
- *Default limit for unknown clients:* anyone could invent ids and get a fresh
  budget, and the brief says the header *authorises* a specific client.
- *`{"succes": true}`:* reproduces a typo that the brief's own example contradicts.

**Consequences.**
- Authentication runs before rate limiting, so unknown clients never create
  counter state.
- Headers added beyond the brief, which leave the required bodies unchanged:
  - `Retry-After` (whole seconds) on every 429;
  - `X-Queue-Wait-Ms` on every `/bar` response (see 0002).

**What would make this wrong.** Callers' automated checks expect the literal
`succes` key, or expect unknown clients to be rate limited rather than rejected.
