# SentinelCommerce (zero-cost edition)

A small order / inventory app whose real purpose is to demonstrate **five
cloud-architecture capabilities** live, on a real AWS account in
**ap-south-1 (Mumbai)** — architected to cost a **genuine $0** to build and
demo (no payment method on the account; no charge can be risked).

| Act | Capability | Mechanism (zero-cost) |
|-----|------------|-----------------------|
| 1 | Resilience / disaster recovery | RDS **read-replica promotion** (`promote-read-replica`) — a manual DR action, **not** automatic Multi-AZ HA (that costs). Explained as a deliberate tradeoff. |
| 2 | Real-time event pipeline | DynamoDB Streams → Lambda → SNS low-stock alert (unchanged) |
| 3 | Blocked web attack | API Gateway **Lambda REQUEST authorizer** — regex signatures + per-IP DynamoDB rate limit. Same outcome as WAF, $0. |
| 4 | Self-healing governance | `security_group_watchdog` Lambda on a 5-min EventBridge schedule revokes unrestricted SSH and publishes the fix. Replaces AWS Config + SSM Automation. |
| 5 | Cost / budget discipline | `CfnBudget` + SNS email — now a *stronger* point: genuinely $0, not "cheap" |

> Nothing deploys until you explicitly say **"deploy now."** See
> [RUNBOOK.md](RUNBOOK.md) for the demo and [TEARDOWN.md](TEARDOWN.md) to
> remove everything. Free-tier basis for every resource: [AUDIT.md](AUDIT.md).

## Manual step (operator does this once, before `cdk deploy`)

```bash
aws ssm put-parameter --name /sentinelcommerce/db-password \
  --type String \
  --value "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" \
  --region ap-south-1
```

**Why `String`, not `SecureString`:** the two RDS-facing Lambdas run in
isolated subnets and can't call the SSM API at runtime without a **paid**
VPC interface endpoint (~$7/mo) or a NAT gateway. Instead the password is
injected into their environment **at deploy time** via the CloudFormation
`{{resolve:ssm:...}}` dynamic reference, which only resolves `String`
parameters. SSM `String` parameters are $0 — identical to `SecureString`
for cost — but the value is **not encrypted at rest in SSM** and is visible
in the Lambda configuration. Acceptable for a throwaway demo DB in an
isolated subnet. Nothing else is needed from the operator.

## Stack layout

One CDK App (`app.py`), seven `Stack` classes, `cdk deploy --all`.
Dependency order CDK resolves:

```
NetworkStack → DataStack → SecurityStack → ComputeStack → ObservabilityStack → CostStack
GovernanceStack  (independent; ComputeStack depends on it for one SSM parameter)
```

## Design decisions & why

### NetworkStack — $0
* VPC `10.0.0.0/16`, 2 AZs, **only `PRIVATE_ISOLATED` subnets**.
* **No NAT gateway** (`nat_gateways=0`) — no free tier, ~$0.045/hr otherwise.
* **No `PRIVATE_WITH_EGRESS` tier** — it only makes sense with a NAT. Only
  RDS and the two RDS-facing Lambdas go in the VPC; every other Lambda uses
  default networking and reaches DynamoDB/SNS/SSM over the public AWS API.
* VPC, subnets, route tables, security groups: always free.

### DataStack — RDS free tier + free DynamoDB
* **Standard `rds.DatabaseInstance`, MySQL 8.0, `db.t4g.micro`**,
  `multi_az=False`, `allocated_storage=20`, isolated subnets. Aurora is
  gone (no free tier; blocked on this account anyway).
* Storage encrypted with the AWS-managed `aws/rds` key (no monthly fee;
  KMS requests inside the always-free 20,000/month). **No customer key.**
* **No `from_generated_secret()`** — that creates a Secrets Manager secret
  ($0.40/mo). Master password comes from the operator-created SSM `String`
  parameter (see Manual step).
* **DynamoDB** single table, `PK`/`SK`, on-demand, **default encryption
  (AWS-owned key, free)** — not `AWS_MANAGED`, not `CUSTOMER_MANAGED`.
  Streams `NEW_AND_OLD_IMAGES`, PITR on (free).
* ⚠️ **The RDS free tier is 12-month, not permanent** (750 hrs/mo of
  `db.t4g.micro` + 20 GB). $0 for the course; see AUDIT.md. Running the
  primary **and** the Act 1 read replica for a whole month would exceed
  750 hrs — delete the replica after the demo.

### SecurityStack — almost empty now
* KMS CMK: **removed** ($1/mo). WAFv2 WebACL: **removed** ($5/mo + $1/rule).
* AWS **Shield Standard** protects every account automatically at no cost —
  nothing to provision.
* All that remains: the unused **`demo-remediation-target` security group**
  (Act 4 blast target; attached to nothing; free).
* The WAF replacement (REQUEST authorizer) lives in **ComputeStack** —
  putting it here would create a Security↔Compute cycle via the
  auto-generated API-Gateway→Lambda invoke permission.

### ComputeStack
* REST API (regional) + Lambdas (Python 3.13):
  * `create_order` → RDS (isolated subnet); `get_reports` → RDS (isolated).
    DB password injected at deploy via `{{resolve:ssm:...}}` → **no runtime
    SSM call, no VPC endpoint, no NAT.**
  * `get_inventory` / `update_cart` / `stream_processor` → **no VPC**,
    default networking, DynamoDB/SNS/SSM over the public API.
* **`request_authorizer`** — API Gateway REQUEST authorizer, the $0 WAF
  replacement:
  * regex signatures (SQLi / XSS / command-injection / path traversal)
    tested against the decoded path + query + headers (API Gateway does
    **not** pass the body to an authorizer);
  * per-IP **fixed-window rate limit** (100 req / 5 min) counted in the
    existing DynamoDB table (`PK="RL#<ip>"`, `SK="WINDOW#<epoch/300>"`,
    with a TTL so counters self-expire — no second table);
  * every Deny → CloudWatch log + `sentinelcommerce-alerts` SNS publish.
* **`security_group_watchdog`** — outside the VPC. EventBridge fires it
  every 5 min (free); also `aws lambda invoke` on demand. It runs
  `ec2:DescribeSecurityGroups` on the one demo SG, and if it finds
  `0.0.0.0/0:22` calls `ec2:RevokeSecurityGroupIngress` then `sns:Publish`.
  IAM role scoped to exactly those actions on that one SG ARN.
* Logs retention 7 days; `sentinelcommerce-inventory-alerts` +
  `sentinelcommerce-alerts` SNS topics.

### GovernanceStack — Parameter Store only
* AWS **Config removed** (no free tier). SSM Automation document
  **removed** (the watchdog Lambda does it in boto3). CloudTrail Trail
  **not created** — the demo uses the account's built-in, always-free
  **90-day Event History** (`aws cloudtrail lookup-events`).
* Keeps the genuine "Systems Manager" component: the
  `/sentinelcommerce/low-stock-threshold` standard parameter (free).

### ObservabilityStack — free-tier CloudWatch only
* One dashboard `SentinelCommerce-MissionControl`: RDS CPU / connections /
  freeable memory, DynamoDB consumed capacity, each Lambda's errors + p99,
  the authorizer's block count (Logs Insights) and the watchdog's
  invocations/errors + a Logs Insights view of its remediation events.
* CloudTrail metric filter + alarm **removed** (Trail is gone). Zero custom
  metrics, zero alarms — inside the free tier (3 dashboards, 10 alarms).

### CostStack — unchanged
* `CfnBudget` (monthly, default $10 from context), 80% actual / 100%
  forecast → email + SNS. First 2 budgets/account free.
* Email subscribed to `sentinelcommerce-alerts` +
  `sentinelcommerce-inventory-alerts`.

## Tagging

App-level `Project=SentinelCommerce` / `Environment=demo` on every resource.

## Prerequisites

* IAM user with the needed permissions, `aws configure` profile
  `sentinelcommerce-agent`, region ap-south-1.
* `cdk bootstrap` done. `python3 -m venv .venv && pip install -r requirements.txt`.
* The Manual step above, before `cdk deploy`.

## Layout

```
app.py  cdk.json  requirements.txt
stacks/   network / data / security / compute / governance / observability / cost
lambda/   create_order/ get_reports/ inventory/ stream_processor/ authorizer/ sg_watchdog/
layers/pymysql/            pure-Python PyMySQL Lambda layer
scripts/  seed_data.py   demo_check.sh
RUNBOOK.md  TEARDOWN.md  AUDIT.md
```
