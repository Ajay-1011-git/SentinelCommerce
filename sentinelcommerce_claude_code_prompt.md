# SentinelCommerce — Build Prompt for Claude Code

Paste everything below into Claude Code as your first message in a fresh project folder.

---

## Context

I'm building **SentinelCommerce** for a Cloud Architecture & Design course project. It's a small order/inventory app whose real purpose is demonstrating five things live to my professor: database failover, a real-time event pipeline, a blocked web attack, self-healing governance (AWS Config auto-remediating a misconfiguration via Systems Manager), and cost/budget discipline. This will be deployed to a **real personal AWS account** (ap-south-1, Mumbai) — not a sandbox — so treat every deploy as something that costs real money and follow the safety rules in the last section exactly.

Prerequisites already done on my end: dedicated IAM user with admin access configured via `aws configure`, CDK CLI installed, `cdk bootstrap` already run in this account/region, a manual Budget alert already set as a safety net.

## Tech stack & conventions

- **AWS CDK v2, Python 3.12**, one CDK App composed of separate Stack classes deployed together via `cdk deploy --all`, in this dependency order: `NetworkStack` → `DataStack` → `SecurityStack` → `ComputeStack` → `GovernanceStack` → `ObservabilityStack` → `CostStack`.
- Region and account come from CDK context/environment variables — never hardcode an account ID or region string in stack code.
- Tag every resource in the app with `Project=SentinelCommerce` and `Environment=demo` at the App level (`cdk.Tags.of(app).add(...)`), so cost is traceable and Trusted Advisor/Cost Explorer views are clean.
- Every Lambda gets its own least-privilege IAM role via CDK grants (`table.grant_read_write_data(fn)` style) — never attach a managed admin policy to an app-layer role.

## 1. NetworkStack

- VPC, CIDR `10.0.0.0/16`, 2 AZs.
- Three subnet groups: `PUBLIC` (for the NAT gateway), `PRIVATE_WITH_EGRESS` (for Lambdas that need outbound internet), `PRIVATE_ISOLATED` (for Aurora — no route out at all).
- **Exactly one NAT gateway** (`nat_gateways=1`), not one per AZ — this is a demo, not production, and NAT gateways are billed hourly regardless of use. Note this tradeoff in a code comment.

## 2. DataStack

**Aurora:**
- Aurora MySQL-Compatible cluster (latest 3.x engine version), 1 writer + 1 reader instance, one in each AZ — this reader is simultaneously your Multi-AZ failover target *and* your read replica; there's no separate "read replica" resource needed because in Aurora those are the same mechanism. Say this explicitly in a comment — it's a good architecture point to be able to explain live.
- Instance class: smallest Aurora supports for provisioned (`db.t4g.medium` or `db.t3.medium` — flag that Aurora has no free-tier/micro option, unlike standard RDS, so this only runs during build/demo windows).
- Storage encrypted using the CMK from SecurityStack.
- Credentials: `from_generated_secret()`, stored in Secrets Manager automatically — never hardcode a password anywhere.
- Enable automatic rotation via the cluster's single-user rotation helper, but make sure a rotation can also be **triggered manually on demand** (I'll do this live in the demo to show the old credential immediately fail).
- Deployed into the isolated subnet group.

**DynamoDB:**
- Single table, generic `PK`/`SK` design, `PAY_PER_REQUEST` billing.
- Server-side encryption using the same CMK.
- Streams enabled with `NEW_AND_OLD_IMAGES`.
- Point-in-time recovery on (cheap, good practice — mention it in the README as a Module 4 talking point).

## 3. SecurityStack

- **KMS**: one customer-managed key, rotation enabled, alias `alias/sentinelcommerce`. Key policy grants decrypt only to the specific Lambda execution roles that need it plus account admin — not `"*"`. This one key encrypts Aurora storage, DynamoDB, and the Secrets Manager secret; note in a comment that this is a deliberate "one key, three layers" defense-in-depth talking point.
- **WAFv2**: a REGIONAL WebACL with, in priority order: `AWS-AWSManagedRulesCommonRuleSet`, `AWS-AWSManagedRulesSQLiRuleSet`, then a custom rate-based rule blocking any single IP over 100 requests/5 minutes. Default action Allow. Enable CloudWatch metrics and sampled requests on the ACL and every rule. Associate it to the API Gateway stage created in ComputeStack (REST API, not HTTP API — WAF association support for REST APIs is the most reliable path, confirm current AWS documentation before finalizing this if anything looks off).
- **A dedicated, otherwise-unused "demo-remediation-target" security group** — not attached to Aurora, Lambda, or anything real. This exists purely so I can safely open port 22 to 0.0.0.0/0 on it during the governance demo without ever touching the security group that actually protects the database.

## 4. ComputeStack

REST API Gateway (regional endpoint) + these Lambda functions, Python 3.13, deployed into `PRIVATE_WITH_EGRESS` subnets:

- `create_order` — writes to Aurora via the writer endpoint.
- `get_reports` — reads from Aurora via the **reader endpoint explicitly** (comment why: isolates report traffic from checkout traffic).
- `get_inventory` / `update_cart` — reads/writes DynamoDB.
- `stream_processor` — DynamoDB Streams event source mapping; on a stock decrement crossing a low-stock threshold (read from Systems Manager Parameter Store, not hardcoded), publish to the `sentinelcommerce-inventory-alerts` SNS topic.

Each function: env vars for table name / cluster endpoints / secret ARN, structured JSON logging, CloudWatch Logs retention set to 7 days (cost).

## 5. GovernanceStack

- **AWS Config**: configuration recorder + delivery channel to a new S3 bucket (lifecycle rule expiring objects after 7 days). Before creating the recorder, the deployment should first check via `aws configservice describe-configuration-recorders` whether one already exists in this account/region and halt with a clear message if so.
- **Config rule**: the managed rule that flags unrestricted inbound SSH (verify the exact current AWS source identifier in the AWS Config documentation rather than assuming one — names have shifted before), scoped to the demo security group from SecurityStack.
- **Remediation**: write a **custom SSM Automation document** (don't rely on a specific AWS-managed remediation document existing under a guessed name — write our own so it's fully under our control for the demo). Steps: `aws:executeAwsApi` calling `ec2:RevokeSecurityGroupIngress` to remove the 0.0.0.0/0:22 rule from the flagged security group ID (passed in via the remediation configuration's `ResourceValue`/`RESOURCE_ID` parameter), then `aws:executeAwsApi` calling `sns:Publish` to log what was fixed to the governance alerts topic.
- Link the Config rule to this document via a `RemediationConfiguration`, automatic=true.
- **Systems Manager Parameter Store**: store the low-stock threshold and any other small non-secret config values here instead of hardcoding them into Lambda env vars.

## 6. ObservabilityStack

- **CloudWatch dashboard** named `SentinelCommerce-MissionControl` with widgets for: Aurora CPU + DB connections (writer and reader), DynamoDB consumed capacity, each Lambda's errors and p99 duration, WAF allowed vs blocked request counts, and a Logs Insights widget over the remediation Lambda/document's execution logs showing recent auto-remediation events.
- **CloudTrail**: single trail, single-region (fine for this scope, say so explicitly), delivering to both an S3 bucket (14-day lifecycle expiry) and a CloudWatch Logs group.
- A metric filter on that log group for `{ $.eventName = "AuthorizeSecurityGroupIngress" }` feeding a CloudWatch alarm that publishes to `sentinelcommerce-alerts` SNS topic.

## 7. CostStack

- `CfnBudget`: monthly cost budget (amount from context, default $10), notifications at 80% actual and 100% forecasted, both to my email via SNS.
- Subscribe my email (passed via CDK context, not hardcoded) to `sentinelcommerce-alerts` and `sentinelcommerce-inventory-alerts`.

## Deliverables — file structure

```
sentinelcommerce/
  app.py
  cdk.json
  requirements.txt
  stacks/
    network_stack.py, data_stack.py, security_stack.py,
    compute_stack.py, governance_stack.py, observability_stack.py, cost_stack.py
  lambda/
    create_order/, get_reports/, inventory/, stream_processor/
  scripts/
    seed_data.py          # puts a few demo products/orders in
    demo_check.sh          # read-only smoke test confirming every Act's mechanism is wired
  RUNBOOK.md               # exact CLI command for every one of the 5 demo Acts, with expected output/timing
  README.md                # architecture explanation + why each decision was made + cost notes
  TEARDOWN.md              # cdk destroy plus anything CDK won't auto-clean (S3 objects, KMS deletion window, log groups)
```

`demo_check.sh` should, after a deploy, run read-only checks confirming: Aurora cluster status is `available` with 2 members, the WAF WebACL is associated with the API stage, the Config rule exists and its remediation configuration is attached, the DynamoDB stream event source mapping is `Enabled`.

`RUNBOOK.md` must include the exact command to force an instant Config re-evaluation (`aws configservice start-config-rules-evaluation`) rather than waiting on the default schedule — otherwise the governance demo will stall live.

## Safety rules — follow these exactly

1. **Do not run `cdk deploy` or any resource-creating AWS CLI command until I explicitly say "deploy now."** Write, `cdk synth`, and validate everything first.
2. Before writing any code, run `aws sts get-caller-identity` and show me the account ID and region so we both confirm we're targeting the right account.
3. After a successful `cdk synth`, give me a plain-English list of every billable resource this will create and its approximate hourly cost, so I can sanity-check before deploying.
4. For any S3 bucket created for this demo (Config delivery, CloudTrail logs), set `auto_delete_objects=True` and `removal_policy=RemovalPolicy.DESTROY` so `cdk destroy` actually cleans them up — call this out explicitly since it's a demo, not production.
5. If you're not certain of an exact AWS-managed resource name, rule identifier, or API parameter (e.g. the Config managed rule name for restricted SSH), say so and verify against current AWS documentation rather than guessing.
