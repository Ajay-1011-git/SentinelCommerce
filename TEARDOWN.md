# SentinelCommerce — TEARDOWN

Run this **the same day as the demo**. Nothing in this project is
free-tier — Aurora and the NAT gateway bill every hour they exist.

```bash
export AWS_REGION=ap-south-1
source .venv/bin/activate
```

## 1. Undo any live-demo mutation first

```bash
# If Act 4 was run and remediation did NOT fire, remove the SSH rule by hand
SG=$(aws ec2 describe-security-groups \
  --filters "Name=tag:Project,Values=SentinelCommerce" \
            "Name=description,Values=*demo-remediation-target*" \
  --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 revoke-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0 2>/dev/null || true
```

## 2. Destroy all stacks

```bash
cdk destroy --all --force        # ~15-25 min
```

CDK removes the stacks in reverse dependency order. Because every demo S3
bucket was created with `auto_delete_objects=True` +
`removal_policy=DESTROY`, the Config-delivery and CloudTrail buckets are
**emptied and deleted automatically** — no manual `aws s3 rb` needed.

## 3. Things `cdk destroy` will NOT fully clean — check each

| Resource | Why it lingers | Command |
|----------|----------------|---------|
| **KMS key** | CMKs enter a **pending-deletion window** (7–30 days), they are not deleted immediately. Rotation stops and it stops billing the $1/mo after the window. | `aws kms describe-key --key-id alias/sentinelcommerce --query 'KeyMetadata.[KeyState,DeletionDate]'` — if still `Enabled`, run `aws kms schedule-key-deletion --key-id alias/sentinelcommerce --pending-window-in-days 7` |
| **CloudWatch Log groups** | Lambda log groups created outside CDK's `logRetention` custom resource, and the API Gateway execution log group, can survive. | `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/sentinelcommerce --query 'logGroups[].logGroupName'` then `aws logs delete-log-group --log-group-name <name>` for each; also `/aws/apigateway/`, and the CloudTrail log group if it remains. |
| **AWS Config recorder / delivery channel** | If the stack fails to delete these cleanly they keep recording (and billing per config item). | `aws configservice describe-configuration-recorders` and `... describe-delivery-channels`; if present: `aws configservice stop-configuration-recorder --configuration-recorder-name <n>`, `delete-configuration-recorder`, `delete-delivery-channel`. |
| **Secrets Manager secret** | The Aurora secret has a recovery window (default 30 days, or 7 via CDK). Billed ~$0.40/mo until then. | `aws secretsmanager list-secrets --query "SecretList[?contains(Name,'sentinel') || contains(Name,'Aurora')].[Name,DeletedDate]"`; force now with `aws secretsmanager delete-secret --secret-id <arn> --force-delete-without-recovery` |
| **CloudTrail** | The trail itself is deleted by CDK; confirm no leftover trail keeps writing. | `aws cloudtrail describe-trails --query 'trailList[].Name'` |
| **RDS final snapshot** | `removal_policy=DESTROY` skips the final snapshot, but check for any automated snapshots still retained. | `aws rds describe-db-cluster-snapshots --snapshot-type manual --query "DBClusterSnapshots[?contains(DBClusterIdentifier,'sentinel')].[DBClusterSnapshotIdentifier]"` |
| **CDK bootstrap assets** | The shared `cdk-hnb659fds-*` S3 bucket / ECR repo are **shared infra** — leave them unless you are done with CDK in this account. | — |

## 4. Final cost check (next day)

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u '+%Y-%m-01'),End=$(date -u '+%Y-%m-%d') \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["SentinelCommerce"]}}'
```

Expect the daily figure to drop to ~$0 once the KMS key, secret and any
log groups are gone. If it does not, walk the table above again.
