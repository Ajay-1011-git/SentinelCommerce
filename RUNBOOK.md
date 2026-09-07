# SentinelCommerce — Demo RUNBOOK

Every command for the live demo, with expected output and rough timing.
Region is **ap-south-1** throughout (`export AWS_REGION=ap-south-1`).

---

## 0. Pre-flight (before the professor is watching)

```bash
# 0.1 Confirm the target account
aws sts get-caller-identity
#     -> Account: <your 12-digit account>, Arn: .../<your admin user>

# 0.2 One-Config-recorder safety gate
bash scripts/predeploy_check.sh
#     -> "OK: no existing configuration recorder. Safe to deploy."

# 0.3 Synthesize (no resources created)
source .venv/bin/activate
cdk synth            # ~20 s

# 0.4 Deploy everything  (ONLY after you have decided to spend money)
cdk deploy --all --require-approval never    # ~20-30 min (Aurora dominates)

# 0.5 Seed demo data
export SENTINEL_API_URL=$(aws cloudformation describe-stacks \
  --stack-name SentinelCommerce-ComputeStack \
  --query "Stacks[0].Outputs[?contains(OutputKey,'Endpoint')].OutputValue" --output text)
python scripts/seed_data.py

# 0.6 Read-only smoke test — every Act must PASS
bash scripts/demo_check.sh
```

---

## Act 1 — Database failover (~3–5 min)

**Claim:** the app survives losing the Aurora writer; the reader is
promoted automatically.

```bash
CID=$(aws rds describe-db-clusters \
  --query "DBClusters[?starts_with(DBClusterIdentifier,'sentinelcommerce') || contains(DBClusterIdentifier,'auroracluster')].DBClusterIdentifier | [0]" \
  --output text)

# Show current roles
aws rds describe-db-clusters --db-cluster-identifier "$CID" \
  --query 'DBClusters[0].DBClusterMembers[].[DBInstanceIdentifier,IsClusterWriter]' --output table

# Trigger failover
aws rds failover-db-cluster --db-cluster-identifier "$CID"

# Watch the writer flip (repeat for ~60-120 s)
watch -n 5 "aws rds describe-db-clusters --db-cluster-identifier $CID \
  --query 'DBClusters[0].DBClusterMembers[].[DBInstanceIdentifier,IsClusterWriter]' --output table"
```

**Expected:** within ~60–120 s the `IsClusterWriter=true` flag moves to the
other instance; `create_order` calls succeed again immediately after. The
MissionControl dashboard shows a brief connection dip on the old writer.

---

## Act 2 — Real-time event pipeline (~2 min)

**Claim:** a stock decrement that crosses the low-stock threshold fires a
Stream → Lambda → SNS alert; the threshold is config, not code.

```bash
# Current threshold (SSM Parameter Store — not a Lambda env var)
aws ssm get-parameter --name /sentinelcommerce/low-stock-threshold \
  --query 'Parameter.Value' --output text          # -> 5

# Drop stock on a product below the threshold via the API
curl -s -XPOST "$SENTINEL_API_URL/cart" \
  -H 'content-type: application/json' \
  -d '{"cart_id":"demo","sku":"SC-HUB-03","qty":3}'

# stream_processor logs
aws logs tail /aws/lambda/sentinelcommerce-stream-processor --since 2m --follow
#   -> {"event": "low_stock_published", "sku": "SC-HUB-03", "stock": 3, "threshold": 5}
```

**Expected:** email to the subscribed address from
`sentinelcommerce-inventory-alerts` within ~1 min. Change the threshold
live (`aws ssm put-parameter --name /sentinelcommerce/low-stock-threshold
--value 2 --overwrite`) and show the behaviour shift with no redeploy.

---

## Act 3 — Blocked web attack (~2 min)

**Claim:** WAF blocks SQLi and a request flood before they reach Lambda.

```bash
# 3a. SQL injection attempt -> blocked by AWSManagedRulesSQLiRuleSet
curl -s -o /dev/null -w "%{http_code}\n" \
  "$SENTINEL_API_URL/inventory/1%27%20OR%20%271%27%3D%271"
#   -> 403

# 3b. Rate flood -> blocked by the custom rate-based rule (>100 req / 5 min)
for i in $(seq 1 200); do
  curl -s -o /dev/null -w "%{http_code} " "$SENTINEL_API_URL/inventory"
done; echo
#   -> 200 ... then 403 403 403 once the IP crosses 100

# WAF counters
aws cloudwatch get-metric-statistics --namespace AWS/WAFV2 \
  --metric-name BlockedRequests --start-time "$(date -u -v-15M '+%Y-%m-%dT%H:%M:%SZ')" \
  --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" --period 300 --statistics Sum \
  --dimensions Name=WebACL,Value=sentinelcommerce-web-acl Name=Region,Value=ap-south-1 Name=Rule,Value=ALL
```

**Expected:** `403` responses; `BlockedRequests` climbs on the
MissionControl dashboard "WAF allowed vs blocked" widget.

---

## Act 4 — Self-healing governance (~3–5 min)

**Claim:** open SSH to the world on the demo SG and AWS Config +
a custom SSM Automation document revoke it automatically and log the fix.

```bash
SG=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Project,Values=SentinelCommerce" \
            "Name=description,Values=*demo-remediation-target*" \
  --query 'SecurityGroups[0].GroupId' --output text)

# 4a. Misconfigure: open port 22 to the world
aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0

# 4b. Force an immediate Config re-evaluation (do NOT wait for the schedule)
aws configservice start-config-rules-evaluation \
  --config-rule-names sentinelcommerce-restricted-ssh

# 4c. Watch compliance flip to NON_COMPLIANT then back to COMPLIANT
watch -n 10 "aws configservice get-compliance-details-by-config-rule \
  --config-rule-name sentinelcommerce-restricted-ssh \
  --query 'EvaluationResults[].ComplianceType' --output text"

# 4d. Confirm the rule is gone
aws ec2 describe-security-groups --group-ids "$SG" \
  --query 'SecurityGroups[0].IpPermissions' --output json
#   -> []  (the 0.0.0.0/0:22 rule was revoked by the automation)

# 4e. Remediation execution history
aws configservice describe-remediation-execution-status \
  --config-rule-name sentinelcommerce-restricted-ssh
```

**Expected:** NON_COMPLIANT within ~30–60 s of 4b, automatic remediation
runs, SG rule disappears, `sentinelcommerce-governance-alerts` emails
"Revoked 0.0.0.0/0:22 from sg-…", rule returns to COMPLIANT. The
MissionControl Logs Insights widget shows the Authorize + Revoke pair.

---

## Act 5 — Cost / budget discipline (~2 min)

```bash
# The code-managed budget
aws budgets describe-budget --account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --budget-name SentinelCommerce-Monthly

# Month-to-date spend, filtered to this project's tag
aws ce get-cost-and-usage --time-period Start=$(date -u '+%Y-%m-01'),End=$(date -u '+%Y-%m-%d') \
  --granularity MONTHLY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["SentinelCommerce"]}}'

# The dashboard the professor has been watching all along
echo "https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards:name=SentinelCommerce-MissionControl"
```

**Expected:** budget shows $10 limit with 80%/100% notifications; tagged
spend is a small figure; every resource carries `Project=SentinelCommerce`.

---

## Manual credential-rotation demo (optional, Act 1 add-on)

```bash
SECRET_ARN=$(aws cloudformation describe-stacks --stack-name SentinelCommerce-DataStack \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraSecretArn'].OutputValue" --output text)

# Cache the current password, then force rotation
aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --query SecretString --output text
aws secretsmanager rotate-secret --secret-id "$SECRET_ARN"
# The previously-cached password now fails to authenticate; the app keeps
# working because create_order/get_reports fetch the secret per invocation.
```

---

## Teardown

See [TEARDOWN.md](TEARDOWN.md). Run it **the same day**.
