# DEMO DAY — SentinelCommerce, start to finish

**Read this top to bottom once before you present.** Every command is copy-pasteable.
Nothing here requires you to remember an ID.

- **Region for everything: `ap-south-1` (Mumbai).** If the console shows a different
  region you will see an empty account and panic for no reason.
- **Demo order is 2 → 3 → 4 → 5 → 1.** Deliberate: the safe, repeatable Acts first;
  the one irreversible action (Act 1) last.
- Total run time: **12–18 minutes** of demo, plus Q&A.

---

## 0. Who to log in as

### 0.1 AWS Console — log in as the **IAM user**, not root

| Field | Value |
|---|---|
| Sign-in URL | `https://292759875802.signin.aws.amazon.com/console` |
| Account ID | `292759875802` |
| IAM user name | `Ajay` |
| Password | the one from your `Ajay_credentials` CSV |
| Region (top-right selector) | **Asia Pacific (Mumbai) ap-south-1** |

**Do NOT log in as the root user.** Two reasons, and the second one is a talking point:

1. Root can't be restricted and shouldn't be used for daily work.
2. If anyone asks "how are you managing access?", the answer is:
   *"I'm not using the root account. I created a dedicated IAM user with a scoped
   policy, and the CLI uses a named profile bound to that same identity — root is
   reserved for account-level operations only."*

> If the console asks for "Account ID or alias" on a generic sign-in page, use the
> sign-in URL above instead — it pre-fills the account and takes you straight to the
> IAM-user login form.

### 0.2 Terminal — the CLI profile

The CLI uses the **same IAM user `Ajay`**, through the named profile
**`sentinelcommerce-agent`**. You do not need to log in again; the access key is
already in `~/.aws/credentials`.

Prove it to yourself (and to the room, if you like) with the first command in §1.

---

## 1. T-minus 60 minutes — terminal setup and preflight

Open **one** terminal. Paste this block once. Leave this terminal open all demo.

```bash
cd ~/Downloads/CloudProject
export AWS_PROFILE=sentinelcommerce-agent
export AWS_REGION=ap-south-1
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
source .venv/bin/activate

aws sts get-caller-identity
```

**Expect exactly:**
```json
{
    "UserId": "AIDAUIKOZGDNJYTYLPWDX",
    "Account": "292759875802",
    "Arn": "arn:aws:iam::292759875802:user/Ajay"
}
```
If `Arn` ends in `:root` — stop, you are on the wrong credentials.

### 1.1 Confirm everything is deployed and healthy

```bash
./scripts/demo.sh status
```
Shows: the 7 stacks, your API endpoint, the RDS instances, the demo security group,
the live rate-limit counter, and current stock. **Read the RDS table carefully** —
you need **two** instances: the primary, and `sentinelcommerce-replica` whose
`replicaOf` column points at the primary.

```bash
./scripts/demo.sh check
```
**Expect:** `== ALL CHECKS PASSED ==` — 10 PASS lines, zero FAIL.

### 1.2 Reset to a clean starting state — **run this again ~2 minutes before you present**

```bash
./scripts/demo.sh reset
```
This does three things that make the demo reliable:
- restocks `SC-HUB-03` to **6** (so Act 2's drop to 3 genuinely crosses the threshold of 5),
- clears your IP's **rate-limit counter** (so Act 3's burst starts from zero),
- removes any leftover `0.0.0.0/0:22` rule on the demo security group.

> **Why this matters:** if you rehearse and then present without resetting, Act 2 won't
> fire an alert (stock is already below threshold, so it never *crosses*) and Act 3's
> rate limiter may block instantly instead of demonstrating the crossing. Reset is the
> single most important pre-demo step.

---

## 2. Browser tabs — open these before you start, in this order

Log in (§0.1), set region to **Mumbai**, then open each in its own tab:

| # | Where | What you'll point at |
|---|---|---|
| 1 | **CloudWatch → Dashboards → `SentinelCommerce-MissionControl`** | Your main screen. Keep it up most of the demo. |
| 2 | **CloudWatch → Log groups** | You'll open 3 of these: `/aws/lambda/sentinelcommerce-stream-processor`, `…-request-authorizer`, `…-security-group-watchdog` |
| 3 | **RDS → Databases** | Two instances. Act 1's proof. |
| 4 | **DynamoDB → Tables → Explore items** | The inventory + the rate-limit counter rows |
| 5 | **Lambda → Functions** | Filter the list by typing `sentinelcommerce` — **7 functions** |
| 6 | **EC2 → Security Groups** | The one described `demo-remediation-target`. Act 4's proof. |
| 7 | **CloudTrail → Event history** | Act 4's *independent* proof |
| 8 | **SNS → Topics** | `sentinelcommerce-alerts`, `sentinelcommerce-inventory-alerts` |
| 9 | **Billing and Cost Management → Budgets** | Act 5. |

> **Tip:** every resource is tagged `Project = SentinelCommerce`. If you lose something,
> use the console's tag filter, or **Resource Groups & Tag Editor → Find resources**.

---

## 2b. Today's live resource values

Deployed **2026-09-11**, account `292759875802`, region `ap-south-1`.
*(These change on every redeploy — `./scripts/demo.sh status` always prints the current set.)*

| What | Value |
|---|---|
| API endpoint | `https://mqini1l861.execute-api.ap-south-1.amazonaws.com/prod` |
| RDS primary | `sentinelcommerce-datastack-sentineldbcd10063e-kha9qlo5pqzv` |
| RDS replica (Act 1 target) | `sentinelcommerce-replica` |
| DynamoDB table | `SentinelCommerce-DataStack-SentinelTable15FE6C31-1WT2F5AILILQI` |
| Demo security group | `sg-00a548cdff978c387` |
| CloudWatch dashboard | `SentinelCommerce-MissionControl` |
| Budget | `SentinelCommerce-Monthly` ($10) |

**Pre-demo smoke test: `== ALL CHECKS PASSED ==` (11/11).**

**Rehearsal results from this morning — all verified working:**

| Act | Verified result |
|---|---|
| 2 | stock 6 → 3, `low_stock_published` for `SC-HUB-03` in ~8s |
| 3a | SQLi → `HTTP 403`, `signature:'\s*(or\|and)\s+'?\d` logged |
| 3b | burst of 40 → **24× 200, 16× 403**, counter 1 → 41, `rate_limit:41/25` logged |
| 4 | SSH opened → watchdog returned `{"revoked": 1}` → rules back to `[]` |
| 5 | budget $10 MONTHLY, month-to-date spend **$0.00** |
| 1 | replica `available` and replicating — **Act 1 ready**. *Not rehearsed: irreversible, saved for the live demo.* |

---

## 3. Opening line

> "This is a small ordering app, but the actual project is the infrastructure behind
> it. I built it to demonstrate database resilience, real-time event processing,
> active security, and self-healing governance — and I'm going to trigger each of
> those live rather than just describe them. Everything you'll see costs genuinely
> $0 to run, and that was a real architecture constraint I had to design around,
> not an afterthought."

---

## 4. THE ACTS

Each Act below has the same four blocks: **SAY → RUN → EXPECT → SHOW & SAY**.
Run every command from the terminal you set up in §1.

---

### ACT 2 — Real-time inventory pipeline *(do this first)*

**SAY:**
> "When stock drops below a threshold, the system should notice within seconds —
> with nobody polling for it. And the threshold itself is configuration, not code."

**RUN:**
```bash
./scripts/demo.sh act2
```

**EXPECT** — the script prints, in order:
1. `threshold = 5   (from SSM Parameter Store, not hardcoded)`
2. `SC-HUB-03 stock = 6`
3. the `POST /cart` request and its JSON response
4. `SC-HUB-03 stock = 3`
5. after 5–20 seconds:
   ```
   ALERT PUBLISHED:
      {"event": "low_stock_published", "alert": "LOW_STOCK", "sku": "SC-HUB-03", "stock": 3, "threshold": 5}
   ```

**SHOW & SAY** — switch to **tab 2**, open the
`/aws/lambda/sentinelcommerce-stream-processor` log group, newest log stream:

> "Nothing polled for this. The write to DynamoDB produced a stream record, DynamoDB
> Streams invoked this Lambda automatically, and the Lambda compared the new stock
> level against a threshold it read from Systems Manager Parameter Store — so the
> threshold can be changed live without redeploying any code. It then published to
> SNS. That's the whole event pipeline, and it ran in under a second."

**Optional flourish** — change the threshold live and show it's config, not code:
```bash
aws ssm put-parameter --name /sentinelcommerce/low-stock-threshold --value 2 --overwrite
aws ssm get-parameter --name /sentinelcommerce/low-stock-threshold --query Parameter.Value --output text
# put it back afterwards:
aws ssm put-parameter --name /sentinelcommerce/low-stock-threshold --value 5 --overwrite
```

---

### ACT 3 — Attack blocked

**SAY:**
> "The system should reject malicious input and abusive traffic *before* it reaches any
> real application logic. This is not AWS's managed WAF — I wrote this myself."

#### Part 1 — SQL injection

**RUN:**
```bash
./scripts/demo.sh act3-sqli
```

**EXPECT:**
```
HTTP 403
{"Message":"User is not authorized to access this resource with an explicit deny in an identity-based policy"}
   BLOCKED (403) - the authorizer denied it before any handler ran
   {"event": "request_blocked", "reason": "signature:'\\s*(or|and)\\s+'?\\d", "ip": "...", "target": "/inventory/1' OR '1'='1"}
```

#### Part 2 — Rate limiting

**RUN:**
```bash
./scripts/demo.sh act3-rate
```

**EXPECT** — a burst of 40 requests, summarised as a count per status code, e.g.:
```
     25 200
     15 403
   200 = allowed, 403 = blocked once the per-IP counter passed 25
```
then `rate_limit:26/25`-style lines from the authorizer's log.

> The exact 200/403 split varies — what matters is that **both appear**, and that the
> 403s start once the counter crosses 25. If you get **all 403s**, your counter hadn't
> reset: say so out loud — *"that's the limiter still holding from my last run, which
> is itself the feature working"* — then run `./scripts/demo.sh reset` and repeat.

**SHOW & SAY** — **tab 2**, `/aws/lambda/sentinelcommerce-request-authorizer` log group:

> "Every rejection is logged with its reason — signature match or rate limit — and
> published to SNS. This is a Lambda REQUEST authorizer sitting in front of every
> route on the API. I built it because AWS WAF costs $5 a month for the web ACL plus
> a dollar per rule, with no free tier at any usage level. Same job — signature
> filtering and per-IP rate limiting — for $0."

**If asked what you gave up:** managed rule groups that AWS keeps updated, and body
inspection — API Gateway doesn't pass the request body to an authorizer, so I match on
path, query string and headers only. *(That's in PROJECT_EXPLAINED.md §3.)*

**SHOW (optional):** **tab 4**, DynamoDB → Explore items → look for `PK` values starting
`RL#` — those are the live rate-limit counters, with a TTL so they self-expire.

---

### ACT 4 — Self-healing governance

**SAY:**
> "If a security group is accidentally misconfigured — say SSH opened to the entire
> internet — the system should notice and fix it with no human involved."

**RUN:**
```bash
./scripts/demo.sh act4
```

**EXPECT**, in order:
1. `BEFORE:` → `[]` (no inbound rules)
2. `MISCONFIGURE: opening SSH (port 22) to 0.0.0.0/0` → `True`
3. rules now show the `0.0.0.0/0` port-22 rule
4. `watchdog returned: {"revoked": 1, "sg_id": "sg-…", "rules_removed": [...]}`
5. `AFTER:` → `[]` — **the rule is gone**
6. `{"event": "watchdog_remediated", "auto_remediation": "REVOKED_UNRESTRICTED_SSH", ...}`

**SHOW & SAY** — **tab 6**, EC2 → Security Groups → `demo-remediation-target` →
Inbound rules → **Refresh**:

> "I opened SSH to the entire internet, and thirty seconds later the rule is gone —
> and I never touched it. A Lambda on a five-minute EventBridge schedule detects the
> rule and revokes it. I invoked it manually just now so you didn't have to watch me
> wait five minutes, but the schedule runs on its own — you can see earlier scheduled
> runs in its log group."

**Then RUN** for the independent proof:
```bash
./scripts/demo.sh trail
```

**SHOW & SAY** — **tab 7**, CloudTrail → Event history → filter *Event name* =
`RevokeSecurityGroupIngress`:

> "This is CloudTrail — independent, tamper-evident evidence of exactly what happened
> and when, not just my own Lambda's word for it. I'm using the built-in 90-day event
> history rather than creating a Trail, because a Trail writes to S3 and that storage
> isn't free."

**⭐ The strongest single moment in the whole demo — point at the `User` column:**

```
AuthorizeSecurityGroupIngress  08:07:42  Ajay
RevokeSecurityGroupIngress     08:07:43  sentinelcommerce-security-group-watchdog
```

> "Look at the identity column. The dangerous rule was created by *me* — the human —
> at 08:07:42. It was revoked one second later by the watchdog Lambda's own execution
> role. Two different identities, independently recorded by AWS, not by my code. That's
> the audit trail a security reviewer would actually ask for."

*(Verified working this morning — the events appeared within seconds, not the usual
5–15 minute lag. If today it lags, fall back to the Lambda log and return later.)*

> ⚠️ **CloudTrail lags 5–15 minutes.** If the Revoke event isn't there yet, say exactly
> that — *"CloudTrail batches events, so this usually shows up within a few minutes;
> the Lambda's own log already shows it"* — and move on. Come back to this tab later.
> **This is the single most likely thing to look "broken" that isn't.**

**If asked why not AWS Config:**
> "Config charges per configuration item recorded and per rule evaluation, with no
> free-tier exception. Functionally I do the same three things — detect, remediate,
> log. What I gave up is event-driven detection: mine polls every five minutes, so
> worst case there's a five-minute exposure window."

---

### ACT 5 — Cost governance

**SAY:**
> "Every piece of this was deliberately built to run at $0 — not cheap, actually free —
> and I still set a budget alert as a safety net, because trusting your own analysis
> without a backstop isn't good cost governance."

**RUN:**
```bash
./scripts/demo.sh act5
```

**EXPECT:** the `SentinelCommerce-Monthly` budget ($10 USD, MONTHLY), the month-to-date
tagged spend, and a per-service summary of what's running and which free tier covers it.

**SHOW & SAY** — **tab 9**, Billing and Cost Management → Budgets:

> "Ten-dollar monthly budget, alerting at 80% actual and 100% forecast. Current spend
> is effectively zero. The services I deliberately avoided — WAF, Aurora, Config,
> Secrets Manager, a customer-managed KMS key, and NAT gateways — all share one
> property: no free tier at *any* usage level. I rebuilt equivalent functionality from
> services that do have one, and I documented exactly what capability I traded away in
> each case."

> **Be precise if pressed:** everything is on a *permanent* free tier **except RDS and
> API Gateway REST**, which are on AWS's **12-month** tier. Volunteer that — it's the
> difference between having checked and having assumed.

---

### ACT 1 — Resilience *(last — this one is irreversible)*

**SAY:**
> "The standard answer here is Aurora with automatic Multi-AZ failover. Aurora has no
> free tier at any size, so I demonstrated a different, genuinely real disaster-recovery
> technique instead: promoting a read replica to a standalone database."

**RUN:**
```bash
./scripts/demo.sh act1
```
It prints the BEFORE state, then **asks you to type `PROMOTE`** to continue. That guard
exists so you can't trigger this by accident while rehearsing.

**EXPECT:**
- BEFORE → `replicaOf: sentinelcommerce-datastack-sentineldb…` (it *is* a replica)
- promotion runs, waits ~2–4 minutes
- AFTER → `replicaOf: None`, `status: available` — **it is now a standalone primary**

**SHOW & SAY** — **tab 3**, RDS → Databases → refresh. The replica's **Role** column
changes from *Replica* to *Instance*:

> "I want to be precise about what this proves. This is a **manual, asynchronous**
> disaster-recovery action — not the automatic, synchronous failover Multi-AZ gives you.
> They solve different problems. Multi-AZ protects uptime automatically in about a
> minute; a promoted read replica is what you reach for in a real outage runbook when
> automatic failover isn't available, and it can lose whatever replication lag existed
> at the moment of promotion. I chose to demonstrate the free one, and I can tell you
> exactly what capability I traded away."

**Closing line:**
> "That's all four capabilities triggered live, on real infrastructure, at zero cost.
> The trade-offs are documented in PROJECT_EXPLAINED.md, and the teardown procedure
> returns the account to nothing running."

---

## 5. Likely questions — short answers

| Question | Answer |
|---|---|
| **Why not Aurora / WAF / Config?** | "All three share one property: zero free tier at any usage level. I rebuilt the behaviour from free-tier parts and documented what I gave up in each case." |
| **Is this production-ready?** | "No, and I don't claim it is. Three things I'd reverse with a budget: manual promotion → Multi-AZ, the plaintext SSM password → IAM database auth, my authorizer → a real WAF." |
| **Shared Responsibility Model?** | "AWS owns the data centres, hypervisor, and managed-service patching. I own my IAM policies, security-group rules, encryption choices, and input validation. The authorizer and the watchdog are me taking my half seriously rather than assuming AWS covers it." |
| **How do you know it won't suddenly cost money?** | "I checked the free-tier terms for every service and can name the allowance covering each — it's in AUDIT.md. And I set a Budget anyway, because trusting your own analysis without a backstop isn't cost governance." |
| **What's the weakest part?** | "The DB password is a plaintext SSM String parameter, because the Lambdas are in isolated subnets and can't reach SSM at runtime without a paid VPC endpoint. The right fix is RDS IAM authentication — no stored password at all." |
| **Why is the database not Multi-AZ?** | "A Multi-AZ standby is a second instance billed 24/7 with no free-tier cover. Single-AZ plus a promotable replica was the $0 way to still demonstrate a real recovery path." |
| **How many Lambdas and why?** | "Seven. Five are application logic, one is the authorizer, one is the governance watchdog. Each has its own execution role with ARN-scoped policies — no shared admin role." |

---

## 6. If something breaks live

| Symptom | What to do |
|---|---|
| **Act 2 shows no alert** | Stock was already below threshold. Say *"let me reset the stock level"*, run `./scripts/demo.sh reset`, re-run `act2`. |
| **Act 3 burst is all 403** | The limiter is still holding from a previous run. Narrate it as a success — *"that's the limiter still active from my last burst"* — then `reset` and re-run. |
| **Act 3 burst is all 200** | You didn't cross 25 yet. Just run `./scripts/demo.sh act3-rate` again immediately — the counter accumulates. |
| **CloudTrail shows nothing** | Expected — it lags 5–15 min. Show the Lambda log group instead and return to the tab later. |
| **A command hangs or errors** | Say plainly: *"that didn't behave as expected — let me show you the log from my rehearsal instead"* and open `DEMO_RESULTS.md`, which has real captured output from a full verified run. **Never fake it.** |
| **Console shows nothing at all** | Wrong region. Set the top-right selector to **Mumbai ap-south-1**. |
| **You lose a resource** | Every resource is tagged `Project = SentinelCommerce`. Use Resource Groups & Tag Editor → Find resources. |

---

## 7. After the demo — tear it down the same day

```bash
# 1. delete the promoted replica (it's a standalone DB now, CDK doesn't own it)
aws rds delete-db-instance --db-instance-identifier sentinelcommerce-replica \
  --skip-final-snapshot --delete-automated-backups

# 2. destroy all 7 stacks
./node_modules/.bin/cdk destroy --all --force

# 3. remove the operator-created parameter
aws ssm delete-parameter --name /sentinelcommerce/db-password

# 4. delete leftover log groups
for g in $(aws logs describe-log-groups \
      --query "logGroups[?contains(logGroupName,'entinel')].logGroupName" --output text); do
  aws logs delete-log-group --log-group-name "$g"
done

# 5. verify nothing is left
aws cloudformation list-stacks \
  --query "StackSummaries[?starts_with(StackName,'SentinelCommerce') && StackStatus!='DELETE_COMPLETE'].StackName" --output text
aws rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text
```
Full checklist with the edge cases: **TEARDOWN.md**.

### Security housekeeping (do this after teardown)
Your IAM access key and console password were shared during setup. Once the demo is
marked, go to **IAM → Users → Ajay → Security credentials** and **delete the access key
and change the console password**. Good hygiene, and it costs you nothing.

---

## 8. Command reference — everything in one place

```bash
# setup (once per terminal)
cd ~/Downloads/CloudProject
export AWS_PROFILE=sentinelcommerce-agent AWS_REGION=ap-south-1
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
source .venv/bin/activate

./scripts/demo.sh status      # what's deployed, endpoints, live counters
./scripts/demo.sh check       # 10-point smoke test -> expect ALL CHECKS PASSED
./scripts/demo.sh reset       # <-- run 2 min before presenting
./scripts/demo.sh seed        # reload demo products (rarely needed)
./scripts/demo.sh replica     # create the Act 1 replica (~10-15 min)

./scripts/demo.sh act2        # real-time pipeline
./scripts/demo.sh act3-sqli   # SQLi blocked
./scripts/demo.sh act3-rate   # rate limit blocked
./scripts/demo.sh act4        # self-healing governance
./scripts/demo.sh trail       # CloudTrail proof for Act 4
./scripts/demo.sh act5        # cost governance
./scripts/demo.sh act1        # promote replica (asks you to type PROMOTE)
```

**Other documents**
- `PROJECT_EXPLAINED.md` — architecture, the full trade-off table, shared
  responsibility, known limitations. **Read §3 and §6 before the demo.**
- `AUDIT.md` — free-tier evidence, proof that no billable resource types exist.
- `DEMO_RESULTS.md` — captured output from a fully verified run (your backup evidence).
- `TEARDOWN.md` — complete teardown checklist.
