# SentinelCommerce — Demo Guide

Read this fully before tomorrow. It assumes zero prior context and tells you exactly what to click, what to type, and what to say.

## 0. Before anything else — check what state the project is actually in

Go back to Claude Code right now and ask it directly:

> "What's the current status? Is everything still deployed, or did teardown already run? Did Act 1's replica promotion finish?"

Depending on the answer:

- **If everything is still deployed and Act 1 finished** — skip to Section 2, you're ready.
- **If teardown already ran** — you need to redeploy tonight, not tomorrow morning. Tell Claude Code: *"Redeploy everything for tomorrow's demo — remind me of the manual SSM parameter step first, then deploy, then run demo_check.sh and confirm 10/10 pass."* Budget at least 30–45 minutes for the RDS instance and replica to finish creating — don't start this the morning of.
- **If you're not sure** — ask Claude Code to run `demo_check.sh` right now and show you the output. If anything fails, that tells you what's missing.

**Do this tonight, not tomorrow morning.** RDS instances take real time to create, and you do not want to be waiting on a replica ten minutes before you present.

## 1. The one thing to say before you touch anything

Open with this, or something close to it — it frames everything that follows and shows you understand the point of the project, not just the mechanics:

> "This is a small ordering app, but the actual project is the infrastructure behind it — I built it to demonstrate database resilience, real-time event processing, active security, and self-healing governance, and I'm going to trigger each of those live rather than just describe them. Everything you'll see costs genuinely $0 to run, which itself was a real architecture constraint I had to design around."

## 2. Console tour — open these tabs now, before you start

Log into the AWS Console, set the region selector (top right) to **Mumbai (ap-south-1)** — if you're looking at the wrong region, you won't see any of your resources and it'll look like nothing was built.

Open these in separate browser tabs, in this order, so you can flip between them without hunting during the demo:

1. **CloudWatch → Dashboards** — find the one named something like `SentinelCommerce-MissionControl`. This is your main screen — keep it visible for most of the demo.
2. **RDS → Databases** — you should see two instances tagged `Project: SentinelCommerce` (use the tag filter if you can't find them by name) — one primary, one replica.
3. **DynamoDB → Tables** — the cart/inventory table.
4. **Lambda → Functions** — filter by the same tag; you should see 7 functions (`create_order`, `get_reports`, `get_inventory`, `update_cart`, `stream_processor`, `security_group_watchdog`, and the authorizer).
5. **EC2 → Security Groups** — find the one named something like `demo-remediation-target`.
6. **SNS → Topics** — the alerts topics.
7. **CloudTrail → Event history** — this is where Act 4's proof comes from.
8. **Budgets** — your $10 budget.
9. A terminal with the AWS CLI configured and ready, plus `curl` available.

## 3. Act-by-act script

For each Act: say the "before" state out loud, run the command, then point at the console screen that proves it happened. Don't just run commands silently — the narration is half the demo.

---

### Act 2 — Real-time inventory pipeline (do this one first — it's the safest, most reliable one)

**Say:** "When stock drops low, the system should notice within seconds, without anyone polling for it."

**Do:** From your terminal, place an order that reduces stock (ask Claude Code for the exact `curl`/API command if you don't have it memorized — you already verified this works, taking stock from 6 to 3).

**Point at:** Switch to the CloudWatch dashboard or the `stream_processor` Lambda's log group — you should see a `low_stock_published` log line appear within seconds of the request. Then show the SNS topic's publish count ticking up.

**Say while pointing:** "That log line means DynamoDB Streams caught the change the instant it happened, triggered this Lambda automatically, and it decided the new stock level crossed the alert threshold — no polling, no delay."

---

### Act 3 — Attack blocked

**Say:** "The system should reject malicious input and abusive traffic before it ever reaches real application logic."

**Do, part 1 (SQL injection):** Send a request with a SQL-injection-style payload in the URL (you already confirmed this returns a 403). Show the terminal output — the 403 status code.

**Do, part 2 (rate limiting):** ⚠️ **Important — check your rate limit counter before doing this live.** You already pushed it to `227/100` in earlier testing today; if that window hasn't reset, even a small burst tomorrow could get blocked immediately, which is actually still a valid demo (it proves the limiter works) but ask Claude Code beforehand what the counter currently reads and what the window duration is, so you know whether you're demonstrating "here's the burst crossing the limit in real time" or "here's it already over the limit from testing" — either is fine, but know which one you're showing so you can narrate it correctly.

**Point at:** The authorizer Lambda's CloudWatch Logs — show the block reason logged for each rejected request.

**Say while pointing:** "This isn't AWS's managed WAF — I wrote this filtering logic myself, in a Lambda that runs before any real request handler, because WAF has no free tier at any usage level. Same job, done for $0."

---

### Act 4 — Self-healing governance

**Say:** "If a security group is accidentally misconfigured — say, SSH opened to the entire internet — the system should notice and fix it without anyone touching it."

**Do:**
1. Open the EC2 → Security Groups console, find `demo-remediation-target`.
2. Add an inbound rule: SSH (port 22), source `0.0.0.0/0`. Say out loud what you just did and why it's dangerous ("this would let literally anyone on the internet attempt to SSH in").
3. Either wait for the 5-minute schedule, or — better for a live demo — go to Lambda, find `security_group_watchdog`, and manually invoke it (`aws lambda invoke ...` or the "Test" button in console).
4. Show the response: `{"revoked": 1}`.
5. Flip back to the Security Groups console and refresh — the rule is gone.

**Point at:** CloudTrail → Event history, filtered to the `RevokeSecurityGroupIngress` event — this is independent, tamper-evident proof of exactly what happened and when, not just your own Lambda's word for it.

**Say while pointing:** "This is a Lambda on a timer instead of AWS Config, because Config charges per rule evaluation with no free-tier exception. Functionally, same outcome: detect, fix, log — with no human in the loop."

---

### Act 1 — Resilience (do this near the end, since it changes real infrastructure)

**Say:** "The standard approach here would be Amazon Aurora with automatic Multi-AZ failover — but Aurora has zero free tier at any usage level, so I used a different, real disaster-recovery technique instead: manually promoting a read replica to a standalone database."

**Do:** Run `aws rds promote-read-replica --db-instance-identifier <your-replica-id> --region ap-south-1` (get the exact identifier from the RDS console or from Claude Code if you don't have it memorized).

**Point at:** RDS console — refresh the replica's page and show its "Role" change from "Replica" to a standalone instance.

**Say while pointing:** "I want to be precise about what this demonstrates: this is a manual, asynchronous disaster-recovery action — not the automatic, synchronous failover Multi-AZ provides. They solve different problems. Multi-AZ protects uptime automatically; a promoted read replica is what you'd reach for in a real outage runbook when automatic failover isn't available. I chose to demonstrate the free one and can explain exactly what capability I traded away."

**⚠️ This one is irreversible for that database instance** — only do it once, and do it during the actual demo or a final rehearsal, not casually while testing beforehand.

---

### Act 5 — Cost governance (closing point)

**Say:** "Every piece of this system was deliberately built to run at $0 — not cheap, actually free — and I still set a budget alert as a safety net."

**Point at:** The Budgets console — show the $10 threshold and that current spend reads effectively zero.

**Say while pointing:** "A few AWS services — WAF, Aurora, Config, a customer-managed encryption key, Secrets Manager — have no free tier at all, at any usage level, so I specifically avoided them and rebuilt equivalent functionality from services that do have a genuine free tier. That trade-off is documented in my report."

## 4. Likely questions and strong answers

**"Why didn't you use Aurora / WAF / AWS Config, since they're the standard tools for this?"**
"I understand what each one does and designed around it — they all share one thing in common: zero free tier at any usage level, unlike everything else in this architecture. I rebuilt the same functional behavior using free-tier components instead, and I can walk through exactly what capability I gave up in each case." (Then reference the trade-off table in PROJECT_EXPLAINED.md if pressed further.)

**"Is this actually production-ready?"**
"No, and I don't claim it is — a few choices here (a publicly-reachable-in-VPC-only database, no CMK-based key rotation policy, manual replica promotion instead of automatic failover) were made specifically to hit $0 for a course project. In a funded environment I'd reverse several of these — Aurora Multi-AZ, a real WAF, a customer-managed KMS key with a scoped policy."

**"What's the Shared Responsibility Model here?"**
"AWS is responsible for the physical security of their data centers and patching the underlying infrastructure my services run on. I'm responsible for everything I configured on top of that — my IAM permissions, my security group rules, my encryption choices, and my application code's own input validation. The Lambda Authorizer and the security watchdog are both examples of me taking my half of that responsibility seriously rather than assuming AWS handles it for me."

**"How do you know this isn't going to suddenly cost money?"**
"Two answers: first, I checked the free-tier terms for every single service in this design and can name which allowance covers each one — that's documented in PROJECT_EXPLAINED.md. Second, I still set a Budget alert as a safety net, because trusting your own analysis without a backstop isn't good cost governance."

## 5. If something breaks live

- **A demo command fails or times out:** say so plainly — "that didn't behave as expected, let me show you the log instead" — and pull up the relevant CloudWatch log group showing a previous successful run (you have `DEMO_RESULTS.md` from your rehearsal as backup evidence). Don't fake it or skip past it silently.
- **Rate limiter blocks something you didn't intend to block:** that's actually evidence the feature works — say exactly that, out loud, instead of treating it as a failure.
- **You lose track of which tab is which:** the tag `Project: SentinelCommerce` is on every resource — use each console's tag filter to relocate anything.

## 6. Last thing — do a full timed rehearsal tonight

Run through Acts 2 → 3 → 4 → 5 → 1 in that order (this order is deliberate: it does the safe, repeatable, non-destructive demos first, and saves the one irreversible action — Act 1 — for last), out loud, at actual talking speed, once, tonight. If it takes longer than you expect, cut detail from your narration, not from which Acts you show.
