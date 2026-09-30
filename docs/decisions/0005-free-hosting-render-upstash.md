---
id: 0005
type: record
title: Host free on Render (two services) with Upstash Redis
status: accepted
deciders: Albert-Louis Cohen
date: 2026-09-30
supersedes:
superseded-by:
---

# 0005 — Free hosting on Render + Upstash

**Context.** Cloud deployment is a stretch goal and must cost nothing.

**Decision.**
- **Render free web services, two of them**, both built from the same code by one
  `render.yaml`: `STORAGE=memory` on one and `STORAGE=redis` on the other.
  Anyone can try both stores without reconfiguring anything.
- **Upstash free Redis** serves as the Redis store for the deployed service.
  `REDIS_URL` is set as a Render secret and is never committed.

**Rejected** (checked September 2026).
- *Fly.io and Railway:* both now require a credit card, with a trial only.
- *Koyeb:* no longer accepting new free sign-ups.
- *Serverless platforms (Vercel, Netlify, Cloudflare Workers):* there is no
  long-lived process, so the memory store would not hold counts.
- *Render's own free Key Value:* no persistence on the free tier.
- *AWS, GCP, Azure, Oracle:* excluded by the no-cost rule, and most need a card.

**Consequences.**
- Free Render services sleep after 15 minutes idle. The first request then takes
  about a minute, and the memory store starts empty. The README warns about this
  and gives a warm-up command.
- 750 free instance-hours a month are shared by both services. That is fine,
  since both sleep when idle.
- The Upstash free tier allows 500K commands a month. Each limit decision is one
  `EVALSHA`.

**What would make this wrong.** Render or Upstash withdraws or cuts back its free
tier before review. Local running remains the primary path in the README either
way.
