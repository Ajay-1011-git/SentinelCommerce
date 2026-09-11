# SentinelCommerce — Architecture & Trade-offs

The defence document. Everything a marker might push on, with the honest answer.

---

## 1. What this project actually is

A small order/inventory API is the *vehicle*. The **project** is the infrastructure
behind it, built with **AWS CDK v2 (Python)** as 7 independently-deployable stacks,
demonstrating four capabilities live:

| Capability | Demonstrated by |
|---|---|
| Database resilience / disaster recovery | RDS read-replica promotion |
| Real-time event processing | DynamoDB Streams → Lambda → SNS |
| Active security at the edge | Lambda REQUEST authorizer (signatures + rate limit) |
| Self-healing governance | EventBridge-scheduled watchdog Lambda that auto-revokes bad SG rules |
| Cost governance | AWS Budgets + an architecture designed for genuine $0 |

**The hard constraint:** no payment method on the account, so the design had to cost a
*genuine* $0 — not "cheap". That constraint drove most of the interesting decisions.

---

## 2. Architecture

```
                     Internet
                        │
            ┌───────────▼────────────┐
            │  API Gateway (REST)    │
            │  regional endpoint     │
            └───────────┬────────────┘
                        │  every route
            ┌───────────▼─────────────────────────┐
            │ Lambda REQUEST authorizer           │  ← the $0 "WAF"
            │  • SQLi/XSS/traversal regex          │
            │  • per-IP rate limit (DynamoDB)      │
            │  • Deny → 403, log + SNS             │
            └───────────┬─────────────────────────┘
                        │ allowed only
      ┌─────────────────┼──────────────────────────┐
      │                 │                          │
┌─────▼──────┐   ┌──────▼───────┐          ┌───────▼────────┐
│ create_order│   │ get_inventory│          │  update_cart   │
│ get_reports │   │  (no VPC)    │          │   (no VPC)     │
│ (in VPC,    │   └──────┬───────┘          └───────┬────────┘
│  isolated)  │          │                          │
└─────┬───────┘          └──────────┬───────────────┘
      │                             │
┌─────▼──────────┐        ┌─────────▼──────────┐
│ RDS MySQL 8.0  │        │  DynamoDB table    │
│ db.t4g.micro   │        │  PK / SK, on-demand│
│ single-AZ, 20GB│        │  Streams enabled   │
│ PRIVATE_ISOLATED        └─────────┬──────────┘
└────────────────┘                  │ NEW_AND_OLD_IMAGES
                                    │
                           ┌────────▼──────────┐
                           │ stream_processor  │──► SNS inventory-alerts ──► email
                           │ threshold from SSM│
                           └───────────────────┘

  EventBridge (rate 5 min) ──► security_group_watchdog ──► ec2:Revoke… + SNS alerts
```

**Networking:** VPC `10.0.0.0/16`, 2 AZs, **only `PRIVATE_ISOLATED` subnets**.
No NAT gateway, no internet gateway route for the data tier. Only RDS and the two
RDS-facing Lambdas are inside the VPC. Every other Lambda runs with default Lambda
networking and reaches DynamoDB/SNS/SSM over the public AWS API — which is free,
whereas a NAT gateway is ~$0.045/hr *and has no free tier at all*.

---

## 3. The trade-off table — services deliberately NOT used

This is the heart of the project. **Every one of these has zero free tier at any
usage level.** For each, I rebuilt the functional behaviour from free-tier parts and
can name exactly what I gave up.

| Standard service | Cost that ruled it out | What I used instead | Capability I actually gave up |
|---|---|---|---|
| **Amazon Aurora** | No free tier at any size; ~$0.08+/hr minimum | RDS `db.t4g.micro`, single-AZ, + a **read replica promoted manually** | **Automatic, synchronous Multi-AZ failover.** Mine is a *manual, asynchronous* DR action. Aurora fails over in ~60–120s unattended; mine needs an operator and can lose in-flight replication lag. |
| **AWS WAF v2** | $5/mo per WebACL + $1/rule/mo + $0.60/M req | **Lambda REQUEST authorizer** — regex signature matching + DynamoDB fixed-window per-IP rate limiter | Managed rule groups that AWS updates against new CVEs; body inspection; bot/fraud control; and **API Gateway does not pass the request body to an authorizer**, so I inspect path/query/headers only. |
| **AWS Config** | $0.003 per configuration item + per rule evaluation; no free tier | **EventBridge-scheduled Lambda** (`security_group_watchdog`) polling every 5 min | Continuous, event-driven detection and a full configuration-history timeline. Mine is polling — worst case 5 minutes of exposure before remediation. |
| **AWS Systems Manager Automation** | Charged per step beyond the free tier | The same watchdog Lambda calling `ec2:RevokeSecurityGroupIngress` in boto3 | Nothing meaningful at this scale — but no reusable, versioned runbook document. |
| **Secrets Manager** | $0.40/secret/month | **SSM Parameter Store** `String` parameter, injected at deploy time via the CloudFormation `{{resolve:ssm:…}}` dynamic reference | **Automatic rotation**, and encryption-at-rest via a CMK. See §6 — this is the weakest point in the design and I say so. |
| **KMS customer-managed key** | $1/month per key | AWS-owned / AWS-managed keys (DynamoDB default, `aws/rds`) | A key policy I control, independent key rotation, and the ability to revoke access to all data by disabling one key. |
| **NAT Gateway** | ~$0.045/hr + $0.045/GB; no free tier | `nat_gateways=0`; only DB-facing Lambdas are in the VPC at all | Outbound internet access from inside the VPC. Nothing in this design needs it. |
| **CloudTrail Trail** | First trail's management events are free, but the **S3 storage** it writes is not | The built-in, always-on, **free 90-day Event History** (`aws cloudtrail lookup-events`) | Retention beyond 90 days, data events, log-file integrity validation, and CloudWatch Logs metric filters/alarms on trail data. |
| **VPC Interface Endpoints** | ~$0.01/hr each per AZ | Kept SSM out of the VPC data path entirely (password injected at deploy, not fetched at runtime) | Private-network access to AWS APIs from inside the VPC. |

---

## 4. Free-tier basis for everything that IS used

| Service | Allowance relied on | Permanent? |
|---|---|---|
| Lambda (7 functions) | 1M requests + 400,000 GB-s / month | **Permanent** ✅ |
| DynamoDB | 25 GB storage | Storage permanent ✅ — **but on-demand *requests* have no always-free allowance**; demo volume ≈ $0.00 |
| SNS | 1M publishes + 1,000 email notifications / month | **Permanent** ✅ |
| EventBridge | Scheduled rules are free; invocations count against Lambda's tier | **Permanent** ✅ |
| CloudWatch | 3 dashboards, 10 alarms, 5 GB logs ingest + storage | **Permanent** ✅ |
| SSM Parameter Store | Standard parameters unlimited & free | **Permanent** ✅ |
| AWS Budgets | First 2 budgets per account | **Permanent** ✅ |
| VPC / subnets / route tables / security groups | Always free | **Permanent** ✅ |
| Data transfer out | 100 GB / month | **Permanent** ✅ |
| **RDS** `db.t4g.micro` + 20 GB | 750 instance-hours + 20 GB storage + 20 GB backup / month | ⚠️ **12-month promotional tier, not permanent** |
| **API Gateway REST** | 1M calls / month | ⚠️ **12-month promotional tier, not permanent** |

**The two honest caveats**, which I volunteer rather than hide:
1. RDS and API Gateway REST are on AWS's **12-month** free tier, not a permanent one.
   Everything else is permanently free. For a course project inside that window, $0.
2. Running **both** the primary and the promoted replica for a full month would exceed
   the 750 instance-hour allowance — which is exactly why the teardown procedure
   deletes the replica.

---

## 5. Shared Responsibility Model — where the line sits here

**AWS is responsible for** the physical data centres, the hypervisor, the managed-service
control planes, patching the MySQL engine and the Lambda runtime, and the durability of
S3/DynamoDB storage.

**I am responsible for** everything I configured on top:

| My responsibility | How I discharged it |
|---|---|
| Network exposure | RDS in `PRIVATE_ISOLATED` subnets — no route to the internet *by construction*, not just by security-group rule |
| Identity & least privilege | Every Lambda has its own execution role with hand-written, ARN-scoped policies. No managed admin policy on any application role. The watchdog's `ec2:RevokeSecurityGroupIngress` is scoped to **one** security-group ARN |
| Input validation | The Lambda authorizer — I do **not** assume AWS filters malicious input for me |
| Configuration drift | The watchdog — I assume misconfiguration *will* happen and built detection + automatic remediation |
| Encryption at rest | Enabled on RDS (AWS-managed key) and DynamoDB (AWS-owned key) |
| Cost | Tagged every resource `Project=SentinelCommerce`, plus a Budget as a backstop |

The authorizer and the watchdog are the two clearest examples of *taking my half of the
model seriously* rather than assuming the cloud provider covers it.

---

## 6. Known limitations — stated before anyone has to ask

1. **The DB password is a plaintext SSM `String` parameter**, visible in the Lambda
   configuration. *Why:* the RDS-facing Lambdas sit in isolated subnets and cannot reach
   the SSM API at runtime without a **paid** VPC interface endpoint or NAT gateway. So the
   value is injected at *deploy* time via `{{resolve:ssm:…}}`, and that dynamic reference
   only resolves `String` — not `SecureString`. *The right fix with any budget:* RDS **IAM
   database authentication** (no stored password at all), or Secrets Manager with rotation.
2. **No automatic failover.** Manual replica promotion only (see §3).
3. **The authorizer cannot inspect request bodies** — API Gateway doesn't provide them to
   REQUEST authorizers. SQLi in a JSON body would not be caught. A real WAF, or moving the
   check into each handler, would close this.
4. **Remediation is polling, not event-driven** — up to a 5-minute exposure window.
5. **Single-AZ database**, so an AZ failure is an outage until the replica is promoted.
6. **Not production-ready, and I don't claim it is.** Points 1–5 are all consequences of
   the $0 constraint, and every one of them has a known, costed fix.

---

## 7. What I'd change with a real budget

| Priority | Change | Buys me |
|---|---|---|
| 1 | Aurora Multi-AZ (or RDS Multi-AZ) | Automatic failover in ~60–120s, no operator |
| 2 | RDS IAM auth or Secrets Manager + rotation | Removes the plaintext-password weakness entirely |
| 3 | AWS WAF in front of API Gateway | AWS-maintained managed rules, body inspection, bot control |
| 4 | AWS Config + conformance packs | Event-driven detection, full config history |
| 5 | Customer-managed KMS key | One key I control across RDS + DynamoDB + secrets; instant revocation |
| 6 | NAT gateway or VPC endpoints | Private AWS-API access from inside the VPC |

---

## 8. Repository map

```
app.py                     CDK app - 7 stacks, tags, env from context
stacks/
  network_stack.py         VPC, isolated subnets only, nat_gateways=0
  data_stack.py            RDS db.t4g.micro + DynamoDB (streams, PITR)
  security_stack.py        the unused demo-remediation-target SG
  compute_stack.py         API GW + 7 Lambdas + authorizer + watchdog + EventBridge
  governance_stack.py      SSM Parameter Store (low-stock threshold)
  observability_stack.py   MissionControl CloudWatch dashboard
  cost_stack.py            AWS Budget + SNS email subscriptions
lambda/
  create_order/ get_reports/      → RDS (in VPC)
  inventory/                      → get_inventory + update_cart (DynamoDB)
  stream_processor/               → Streams consumer, SSM threshold, SNS
  authorizer/                     → the $0 WAF
  sg_watchdog/                    → the $0 Config + SSM Automation
scripts/
  demo.sh                  one driver for the whole live demo
  demo_check.sh            read-only 10-point smoke test
  predeploy_check.sh       verifies the SSM password param before deploy
  seed_data.py             demo products + orders
DEMO_DAY.md                step-by-step demo runbook
PROJECT_EXPLAINED.md       this file
AUDIT.md                   free-tier evidence + forbidden-resource verification
TEARDOWN.md                how to take it all down
```
