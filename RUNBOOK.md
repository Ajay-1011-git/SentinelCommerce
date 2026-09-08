# SentinelCommerce — Demo RUNBOOK (zero-cost edition)

Every command for the live demo, with expected output and rough timing.
`export AWS_REGION=ap-south-1` and `export AWS_PROFILE=sentinelcommerce-agent`
throughout.

---

## 0. Pre-flight

```bash
# 0.1 Confirm the target account (Safety Rule 2)
aws sts get-caller-identity          # Account 292759875802, user/Ajay

# 0.2 MANUAL STEP - create the DB password parameter BEFORE deploying DataStack.
#     String (not SecureString): the isolated-subnet Lambdas read it via the
#     {{resolve:ssm:...}} deploy-time reference, which only works on String.
aws ssm put-parameter --name /sentinelcommerce/db-password \
  --type String --value "$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')" \
  --region ap-south-1
#     (print it once if you want a copy:  aws ssm get-parameter --name /sentinelcommerce/db-password --query Parameter.Value --output text)

# 0.3 Synthesize (no resources created)
source .venv/bin/activate
cdk synth

# 0.4 Deploy  (only after you've said "deploy now")
cdk deploy --all --require-approval never          # ~10-15 min (RDS is the long pole)

# 0.5 Seed demo data
export SENTINEL_API_URL=$(aws cloudformation describe-stacks \
  --stack-name SentinelCommerce-ComputeStack \
  --query "Stacks[0].Outputs[?contains(OutputKey,'Endpoint')].OutputValue" --output text)
python scripts/seed_data.py

# 0.6 Read-only smoke test
bash scripts/demo_check.sh
```

---

## Act 1 — Resilience (manual DR, ~5–10 min)

**Claim:** a read replica can be promoted to a standalone primary to recover
from the loss of the original instance.

> **Say this live:** this is a *manual disaster-recovery* action
> (`promote-read-replica`), **not** automatic Multi-AZ high availability.
> Multi-AZ HA would fail over in ~60–120s with no operator action, but it
> costs (a standby instance runs 24/7 with no free tier). This design
> trades automatic failover for $0 and demonstrates the DR mechanism
> instead — a different capability, worth being explicit about.

```bash
DBID=$(aws cloudformation describe-stacks --stack-name SentinelCommerce-DataStack \
  --query "Stacks[0].Outputs[?OutputKey=='DbInstanceId'].OutputValue" --output text)

# Create a read replica (do this a few minutes before the demo - it takes time)
aws rds create-db-instance-read-replica \
  --db-instance-identifier ${DBID}-replica \
  --source-db-instance-identifier $DBID \
  --db-instance-class db.t4g.micro --no-multi-az

aws rds wait db-instance-available --db-instance-identifier ${DBID}-replica

# --- during the demo: "lose" the primary, then promote the replica ---
aws rds promote-read-replica --db-instance-identifier ${DBID}-replica

aws rds wait db-instance-available --db-instance-identifier ${DBID}-replica
aws rds describe-db-instances --db-instance-identifier ${DBID}-replica \
  --query 'DBInstances[0].[DBInstanceStatus,ReadReplicaSourceDBInstanceIdentifier]' --output text
#   -> available   None      (it is now a standalone primary)

# Repoint get_reports at the promoted instance
NEWEP=$(aws rds describe-db-instances --db-instance-identifier ${DBID}-replica \
  --query 'DBInstances[0].Endpoint.Address' --output text)
aws lambda update-function-configuration \
  --function-name sentinelcommerce-get-reports \
  --environment "Variables={DB_READER_ENDPOINT=$NEWEP,DB_PORT=3306,DB_USER=dbadmin,DB_PASSWORD=<from SSM>,DB_NAME=sentinelcommerce}"
```

**Expected:** replica status `available`, source becomes `None`; `get_reports`
returns data from the promoted instance.
**Cleanup:** delete `${DBID}-replica` after the demo (TEARDOWN.md) — two RDS
instances running a whole month would exceed the 750-hour free allowance.

---

## Act 2 — Real-time event pipeline (~2 min)

Unchanged: DynamoDB Streams → `stream_processor` Lambda → SNS.

```bash
aws ssm get-parameter --name /sentinelcommerce/low-stock-threshold \
  --query 'Parameter.Value' --output text          # -> 5

curl -s -XPOST "$SENTINEL_API_URL/cart" -H 'content-type: application/json' \
  -d '{"cart_id":"demo","sku":"SC-HUB-03","qty":3}'

aws logs tail /aws/lambda/sentinelcommerce-stream-processor --since 2m --follow
#   -> {"event": "low_stock_published", "sku": "SC-HUB-03", "stock": 3, "threshold": 5}
```

**Expected:** email from `sentinelcommerce-inventory-alerts` within ~1 min.
Change the threshold live (`aws ssm put-parameter --name
/sentinelcommerce/low-stock-threshold --value 2 --overwrite`) to show it is
config, not code.

---

## Act 3 — Attack blocked (by the REQUEST authorizer, not WAF, ~2 min)

**Claim:** malicious requests are rejected at the edge before any business
Lambda runs — same outcome as WAF, at $0.

```bash
# 3a. SQL-injection-style payload in the path -> authorizer Deny -> 403
curl -s -o /dev/null -w "%{http_code}\n" \
  "$SENTINEL_API_URL/inventory/1%27%20OR%20%271%27%3D%271"
#   -> 403

# 3b. Rate flood -> fixed-window counter in DynamoDB trips at 100/5min -> 403
for i in $(seq 1 130); do
  curl -s -o /dev/null -w "%{http_code} " "$SENTINEL_API_URL/inventory"
done; echo
#   -> 200 ... 200 then 403 403 403 after the 100th request

# Show the authorizer's decision log (this is what replaces the WAF metric)
aws logs tail /aws/lambda/sentinelcommerce-request-authorizer --since 5m \
  --filter-pattern '{ $.event = "request_blocked" }'
#   -> {"event":"request_blocked","reason":"signature:'\\s*(or|and)...","ip":"...","target":"/inventory/1' OR '1'='1"}
#   -> {"event":"request_blocked","reason":"rate_limit:101/100","ip":"..."}
```

**Expected:** `403`s; matching `request_blocked` log lines; an SNS email per
block; the MissionControl "blocked requests" widget populates.

---

## Act 4 — Self-healing governance (~3 min)

**Claim:** open SSH to the world on the demo SG and the
`security_group_watchdog` Lambda revokes it automatically and logs the fix.

```bash
SG=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Project,Values=SentinelCommerce" \
            "Name=description,Values=*demo-remediation-target*" \
  --query 'SecurityGroups[0].GroupId' --output text)

# 4a. Misconfigure
aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0

# 4b. Force the watchdog NOW instead of waiting for the 5-min schedule
#     (mirrors the old `start-config-rules-evaluation` trick)
aws lambda invoke --function-name sentinelcommerce-security-group-watchdog \
  --payload '{}' /dev/stdout
#   -> {"revoked": 1, "sg_id": "sg-...", "rules_removed": [...]}

# 4c. Confirm the rule is gone
aws ec2 describe-security-groups --group-ids "$SG" \
  --query 'SecurityGroups[0].IpPermissions' --output json
#   -> []

# 4d. The watchdog's log + the built-in CloudTrail Event History (free, no Trail)
aws logs tail /aws/lambda/sentinelcommerce-security-group-watchdog --since 5m
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=RevokeSecurityGroupIngress \
  --max-results 5 --query 'Events[].{time:EventTime,user:Username,name:EventName}'
```

**Expected:** `revoked: 1`, SG rule disappears, `sentinelcommerce-alerts`
email "unrestricted SSH revoked", `watchdog_remediated` log line, and the
Revoke event visible in CloudTrail Event History.

---

## Act 5 — Cost / budget discipline (~2 min)

```bash
aws budgets describe-budget \
  --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --budget-name SentinelCommerce-Monthly

# Month-to-date spend for this project's tag - should be ~$0.00
aws ce get-cost-and-usage \
  --time-period Start=$(date -u '+%Y-%m-01'),End=$(date -u '+%Y-%m-%d') \
  --granularity MONTHLY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["SentinelCommerce"]}}'

echo "https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards:name=SentinelCommerce-MissionControl"
```

**Say this live:** this was architected for genuine $0, not "cheap" — every
billable service sits inside a free tier (see README for which tiers are
permanent vs 12-month). The budget is the safety net if a free-tier limit is
ever exceeded by accident.

---

## Teardown

See [TEARDOWN.md](TEARDOWN.md). Delete the Act 1 read replica too.
