# Build Audit Log

## Zero-cost refactor (current)

Every component with no free tier was removed. `cdk synth` is clean (7
templates, exit 0). **Forbidden resource types — verified ABSENT in all
synthesized templates:**

| Type | Present? |
|------|----------|
| `AWS::RDS::DBCluster` (Aurora) | ❌ none |
| `AWS::EC2::NatGateway` | ❌ none |
| `AWS::KMS::Key` (customer-managed) | ❌ none |
| `AWS::SecretsManager::Secret` | ❌ none |
| `AWS::WAFv2::WebACL` | ❌ none |
| `AWS::Config::*` | ❌ none |

### What replaced what

| Removed (had cost) | Replacement ($0) |
|--------------------|------------------|
| NAT gateway | `nat_gateways=0`; only RDS + 2 Lambdas are in the VPC (isolated), everything else uses default Lambda networking |
| Aurora cluster | `rds.DatabaseInstance` MySQL `db.t4g.micro`, single-AZ, 20 GB |
| Secrets Manager generated secret | SSM `String` parameter `/sentinelcommerce/db-password` (operator-created), injected at deploy via `{{resolve:ssm:...}}` |
| KMS CMK | AWS-owned/AWS-managed keys (DynamoDB default, RDS `aws/rds`) |
| WAFv2 WebACL | API Gateway Lambda REQUEST authorizer: regex signatures + DynamoDB fixed-window per-IP rate limit |
| AWS Config + SSM Automation | `security_group_watchdog` Lambda on a 5-min EventBridge schedule (+ on-demand invoke) |
| CloudTrail Trail + S3 + Logs + metric filter/alarm | built-in 90-day CloudTrail Event History (`lookup-events`) — no resource |

### Free-tier basis for every resource type that could bill at scale (Safety Rule 3)

| Resource | Free-tier basis | Permanent? | Demo risk |
|----------|-----------------|-----------|-----------|
| **RDS** `db.t4g.micro` single-AZ + 20 GB gp2 | 750 instance-hrs/mo + 20 GB storage + 20 GB backup | **12-month only** ⚠️ | $0 now; a 2nd instance (Act 1 replica) left up all month would exceed 750 hrs |
| **API Gateway REST** | 1M REST calls/mo | **12-month only** ⚠️ | demo makes ~hundreds of calls → ~$0; structurally not permanent |
| **DynamoDB** (on-demand) | 25 GB storage always free | storage permanent; **on-demand request pricing has NO always-free allowance** ⚠️ | demo writes/reads ≈ a few thousand → < $0.01; effectively $0, not structurally $0 |
| **Lambda** ×7 app functions | 1M requests + 400,000 GB-s/mo | **permanent** ✓ | negligible |
| **EventBridge** rule (5-min schedule) | scheduled rules free; ~8,640 Lambda invokes/mo | **permanent** ✓ | negligible |
| **SNS** (3 topics) | 1M publishes + 1,000 email notifications/mo | **permanent** ✓ | a few dozen emails |
| **CloudWatch** dashboard ×1, logs | 3 dashboards + 5 GB logs ingest/storage + 10 alarms (0 used) | **permanent** ✓ | well within |
| **AWS Budgets** ×1 | first 2 budgets/account | **permanent** ✓ | 1 used |
| **VPC / subnets / route tables / security groups / RDS subnet group** | always free | **permanent** ✓ | — |
| **Data transfer out** | 100 GB/mo | **permanent** ✓ | demo traffic tiny; no NAT data-processing charge |

### Items to double-check on the AWS Pricing Calculator before deploy (Safety Rule 4)

1. **RDS 12-month free tier** — confirm this account (created 2026-09-07) still
   has RDS free-tier eligibility, and that `db.t4g.micro` is free-tier eligible
   in **ap-south-1** (region matters; it is in most, but verify).
2. **API Gateway REST 1M-calls free tier is 12-month.** If the account is past
   12 months at demo time, REST calls are $3.50/M (still ≈ $0 at demo volume).
3. **DynamoDB on-demand requests are not in any always-free bucket.** If you
   want structurally-$0 DynamoDB, switch both the table and the rate-limit
   usage to provisioned 5 RCU / 5 WCU (inside the permanent 25/25 allowance).
4. **AWS Free Plan account limits.** This account is (or was) on the AWS Free
   Plan, which blocked Aurora. Standard single-AZ `db.t4g.micro` RDS is the
   classic free-tier resource and is expected to work, but it may also require
   the Free Plan's "express configuration" — if `cdk deploy` of DataStack
   fails with a free-plan message, that is the blocker to report, not a bug.

### Deploy prerequisite from the operator

* `aws ssm put-parameter --name /sentinelcommerce/db-password --type String --value <strong> --region ap-south-1`
  — **before** `cdk deploy`. DataStack and the two RDS Lambdas both resolve
  this at deploy time. Nothing else is needed from the operator.

### Safety status

* No `cdk deploy` has been run against the refactored code. Awaiting explicit
  "deploy now".
* The earlier (pre-refactor) partial deployment was fully destroyed —
  `cdk destroy --all` completed; account has no SentinelCommerce resources.
