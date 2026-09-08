# SentinelCommerce — TEARDOWN (zero-cost edition)

Everything here is free-tier, but tear down anyway so nothing counts
against the 12-month RDS / API Gateway allowances.

```bash
export AWS_REGION=ap-south-1
export AWS_PROFILE=sentinelcommerce-agent
source .venv/bin/activate
```

## 1. Undo live-demo mutations first

```bash
# Act 1: delete the read replica / promoted instance (skip final snapshot)
DBID=$(aws rds describe-db-instances \
  --query "DBInstances[?ends_with(DBInstanceIdentifier,'-replica')].DBInstanceIdentifier | [0]" --output text)
[ "$DBID" != "None" ] && aws rds delete-db-instance --db-instance-identifier "$DBID" \
  --skip-final-snapshot --delete-automated-backups

# Act 4: if the watchdog didn't fire, remove the SSH rule by hand
SG=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Project,Values=SentinelCommerce" \
            "Name=description,Values=*demo-remediation-target*" \
  --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 revoke-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 2>/dev/null || true
```

## 2. Destroy all stacks

```bash
cdk destroy --all --force        # ~10-15 min
```

`removal_policy=DESTROY` on the RDS instance and DynamoDB table means CDK
deletes them (no final snapshot). There are **no** S3 buckets, KMS keys,
Secrets Manager secrets, Config recorders, or CloudTrail trails to clean —
they were never created.

## 3. Things `cdk destroy` won't remove — check each

| Resource | Why | Command |
|----------|-----|---------|
| **`/sentinelcommerce/db-password`** | operator-created, not owned by any stack | `aws ssm delete-parameter --name /sentinelcommerce/db-password` |
| **Lambda log groups** | `logRetention` custom resource may leave `/aws/lambda/sentinelcommerce-*` | `for g in $(aws logs describe-log-groups --log-group-name-prefix /aws/lambda/sentinelcommerce --query 'logGroups[].logGroupName' --output text); do aws logs delete-log-group --log-group-name $g; done` |
| **API Gateway execution/access logs** | `/aws/api-gateway/` or `API-Gateway-Execution-Logs_*` | list with `aws logs describe-log-groups --log-group-name-prefix API-Gateway` and delete |
| **RDS automated backups** | retained even with `delete-automated-backups` sometimes | `aws rds describe-db-instance-automated-backups --query 'DBInstanceAutomatedBackups[].DBInstanceIdentifier'` then `delete-db-instance-automated-backup` |
| **CDK bootstrap** (`CDKToolkit`) | shared infra, S3 bucket only, effectively free | leave it unless you're done with CDK in this account |

## 4. Confirm $0

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u '+%Y-%m-01'),End=$(date -u '+%Y-%m-%d') \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["SentinelCommerce"]}}'
```

Should read $0.00 throughout. If not, walk the table above again.
