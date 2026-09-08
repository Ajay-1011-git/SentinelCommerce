# SentinelCommerce

A small order / inventory app whose real purpose is to demonstrate **five
cloud-architecture capabilities** live, on a real AWS account in
**ap-south-1 (Mumbai)**:

| Act | Capability | Where it lives |
|-----|------------|----------------|
| 1 | Aurora database failover | `DataStack` |
| 2 | Real-time event pipeline (DynamoDB Streams → Lambda → SNS) | `DataStack` + `ComputeStack` |
| 3 | Blocked web attack (WAFv2 on API Gateway) | `SecurityStack` + `ComputeStack` |
| 4 | Self-healing governance (AWS Config → custom SSM Automation) | `GovernanceStack` |
| 5 | Cost / budget discipline | `CostStack` + `ObservabilityStack` |

> **This deploys to a real personal AWS account and costs real money.**
> Nothing is deployed until you explicitly run the deploy step. See
> [RUNBOOK.md](RUNBOOK.md) to run the demo and [TEARDOWN.md](TEARDOWN.md)
> to remove everything afterwards.

## Stack layout

One CDK App (`app.py`), seven `Stack` classes deployed together with
`cdk deploy --all`. Narrative / dependency order:

```
NetworkStack → DataStack → SecurityStack → ComputeStack
   → GovernanceStack → ObservabilityStack → CostStack
```

CDK resolves the **real** deploy order from cross-stack references. Two AWS
constraints make the actual order differ slightly from the narrative, and
both are deliberate design decisions:

* **KMS before data.** `DataStack` encrypts Aurora, DynamoDB and the DB
  secret with the customer-managed key in `SecurityStack`, so the key is
  created first. `app.py` builds `SecurityStack` before `DataStack`.
* **No stack cycles around the CMK / WAF / SGs.** Cross-stack CDK `grant_*`
  helpers mutate the *granted* resource's policy with a reference back to
  the grantee, which would create `SecurityStack ⇄ ComputeStack` and
  `DataStack ⇄ ComputeStack` dependency cycles. So:
  * every Lambda permission on the CMK, the DB secret, the DynamoDB table
    and its stream is written as an **explicit least-privilege identity
    policy** on that function's own role (scoped to one ARN, never `*`);
  * the Aurora security group opens the DB port to the **VPC CIDR** rather
    than referencing each Lambda's security group across stacks;
  * the WAF **WebACL** is built in `SecurityStack` but the **association**
    to the REST API stage is created in `ComputeStack`.

## Design decisions & why

### NetworkStack
* VPC `10.0.0.0/16`, 2 AZs, three subnet tiers: `PUBLIC` (NAT),
  `PRIVATE_WITH_EGRESS` (Lambdas), `PRIVATE_ISOLATED` (Aurora — **no route
  to the internet at all**).
* **Exactly one NAT gateway** (`nat_gateways=1`). NAT gateways bill per
  hour (~$0.045/hr in ap-south-1) plus data processing regardless of
  traffic. One NAT is a cost tradeoff appropriate to a time-boxed demo; a
  single-AZ failure could interrupt Lambda egress, which production would
  not accept.

### DataStack
* **Aurora PostgreSQL 16**, 1 writer + 1 reader, one instance per AZ. In
  Aurora the reader **is** both the Multi-AZ failover target **and** the
  read replica — the same shared-storage mechanism. There is deliberately
  no separate "read replica" resource. `get_reports` reads the **reader
  endpoint** explicitly so report traffic never contends with checkout
  writes on the writer.
* **Engine = Aurora PostgreSQL, not MySQL** — forced by the target account
  being on the **AWS Free Plan**, which rejects the Aurora MySQL cluster
  engine (`Available engine types: [aurora-postgresql]`). The architecture
  is unchanged; only the SQL dialect and the Lambda driver (`pg8000`
  instead of `PyMySQL`) differ.
* **Serverless v2, 0.5–2 ACU** — Aurora has no free-tier/micro instance;
  Serverless v2 at minimum capacity is the cheapest way to keep a real
  writer + reader pair (needed for the Act 1 failover demo). Runs only
  during build/demo windows.
* Credentials via `from_generated_secret()` → Secrets Manager. No password
  in code, ever. **Single-user rotation** enabled (30 days) and can be
  **forced on demand** in the demo to show the old credential fail
  instantly.
* **DynamoDB** single table, generic `PK`/`SK`, `PAY_PER_REQUEST`,
  SSE with the same CMK, Streams `NEW_AND_OLD_IMAGES`.
  **Point-in-time recovery is ON** — cheap, good practice, and a Module 4
  data-protection talking point.

### SecurityStack
* **One KMS customer-managed key**, rotation on, alias
  `alias/sentinelcommerce`. It encrypts Aurora storage, the DynamoDB
  table, and the Secrets Manager secret — **"one key, three layers"**
  defense-in-depth: disabling this single key severs access to every data
  surface at once. The key policy is **not** `"*"` — the default policy
  grants account-root admin (break-glass) and enables IAM-identity grants;
  each Lambda role gets a narrow `kms:Decrypt` on this key ARN only.
* **WAFv2 REGIONAL WebACL**, rules in priority order:
  1. `AWS-AWSManagedRulesCommonRuleSet`
  2. `AWS-AWSManagedRulesSQLiRuleSet`
  3. custom **rate-based rule**: block any single IP over **100 requests /
     5 min** (100 is WAF's minimum and makes the demo easy to trigger).
  Default action **Allow**; CloudWatch metrics + sampled requests on the
  ACL and every rule. Associated to the **REST API** stage (REST + WAF
  association via `CfnWebACLAssociation` is the most reliable path).
* **`demo-remediation-target` security group** — attached to **nothing**.
  It exists only so the governance demo can open port 22 to `0.0.0.0/0` on
  a group that protects no real resource. The SG guarding Aurora is never
  touched.

### ComputeStack
* **REST API Gateway** (regional) + Lambda (Python 3.13) in
  `PRIVATE_WITH_EGRESS` subnets:
  * `create_order` → Aurora **writer** endpoint
  * `get_reports` → Aurora **reader** endpoint (isolates report load)
  * `get_inventory` / `update_cart` → DynamoDB
  * `stream_processor` → DynamoDB Streams; on a stock decrement crossing
    the low-stock threshold (**read from SSM Parameter Store**, not
    hardcoded) it publishes to `sentinelcommerce-inventory-alerts`.
* Each function: structured JSON logging, env vars for table / endpoints /
  secret ARN, **CloudWatch Logs retention 7 days** (cost).
* `pg8000` (pure-Python PostgreSQL driver) ships as a Lambda **layer** (`layers/pg8000/`).

### GovernanceStack
* **AWS Config**: recorder (scoped to `AWS::EC2::SecurityGroup` to keep
  cost down) + delivery channel → a new S3 bucket with a **7-day
  expiry** lifecycle rule, `auto_delete_objects=True`,
  `removal_policy=DESTROY`. **Run `scripts/predeploy_check.sh` first** — an
  account/region may hold only one Config recorder and CloudFormation
  cannot express that pre-check.
* **Config rule**: managed rule `INCOMING_SSH_DISABLED` (console name
  *restricted-ssh*), scoped to the demo security group.
  ⚠️ **Verify this source identifier against the current AWS Config
  "List of Managed Rules" docs before deploying** — AWS has renamed rule
  identifiers before.
* **Remediation**: a **custom SSM Automation document** we own end to end
  (rather than guessing at an AWS-managed document name). Steps:
  1. `aws:executeAwsApi` → `ec2:RevokeSecurityGroupIngress` removing the
     `0.0.0.0/0:22` rule from the flagged SG (`RESOURCE_ID` from the
     remediation config);
  2. `aws:executeAwsApi` → `sns:Publish` logging the fix to
     `sentinelcommerce-governance-alerts`.
  Linked to the rule via `RemediationConfiguration`, `automatic=true`.
* **SSM Parameter Store** holds the low-stock threshold and other small
  non-secret config.

### ObservabilityStack
* **CloudWatch dashboard `SentinelCommerce-MissionControl`**: Aurora CPU +
  connections (writer & reader), DynamoDB consumed capacity, each Lambda's
  errors + p99 duration, WAF allowed vs blocked, and a Logs Insights
  widget over the CloudTrail log group showing recent
  Revoke/Authorize security-group-ingress events (the auto-remediation
  trail).
* **CloudTrail**: single trail, **single-region** — fine for this scope
  (the whole demo is in ap-south-1; a multi-region trail only adds S3
  cost). Delivers to an S3 bucket (**14-day** expiry,
  `auto_delete_objects`, `DESTROY`) **and** a CloudWatch Logs group.
* **Metric filter** `{ $.eventName = "AuthorizeSecurityGroupIngress" }` on
  that log group → CloudWatch alarm → `sentinelcommerce-alerts` SNS topic.

### CostStack
* **`CfnBudget`** monthly cost budget (amount from CDK context, default
  **$10**), notifications at **80% actual** and **100% forecasted**, each
  to email **and** an SNS topic (`budgets.amazonaws.com` is granted
  `SNS:Publish`). This is a second, code-managed layer on top of the
  manual console budget already set as a safety net.
* Subscribes the notification email (from CDK context, never hardcoded) to
  `sentinelcommerce-alerts` and `sentinelcommerce-inventory-alerts`.

## Tagging & cost traceability

Every resource in every stack is tagged at the App level with
`Project=SentinelCommerce` and `Environment=demo` so Cost Explorer /
Trusted Advisor views stay clean.

## Approximate running cost (build/demo window)

See the plain-English billable-resource list produced after `cdk synth`
(also summarized in [RUNBOOK.md](RUNBOOK.md)). The dominant line items are
Aurora (2 × `db.t3.medium`, ~$0.08/hr each + storage), the single NAT
gateway (~$0.045/hr + data), and AWS Config ($0.003 per config item
recorded). **Tear down the same day** — nothing here is free-tier.

## Prerequisites

* Dedicated IAM user with admin, configured via `aws configure`
* AWS CDK v2 CLI, `cdk bootstrap` already run in this account/region
* Python 3.12+ and a virtualenv:
  ```
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  ```

## Layout

```
app.py                      one CDK App, seven stacks
cdk.json
requirements.txt
stacks/                     network / data / security / compute /
                            governance / observability / cost
lambda/                     create_order/ get_reports/ inventory/ stream_processor/
layers/pg8000/              pure-Python pg8000 (PostgreSQL) Lambda layer
scripts/
  predeploy_check.sh        one-Config-recorder safety gate (run before deploy)
  seed_data.py              demo products + orders
  demo_check.sh             read-only post-deploy smoke test (all 5 Acts)
RUNBOOK.md                  exact CLI for each of the 5 demo Acts
TEARDOWN.md                 cdk destroy + manual cleanup
```
