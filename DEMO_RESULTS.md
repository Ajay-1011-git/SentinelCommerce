# SentinelCommerce — Deploy & Demo Results

**Account** 292759875802 (`ajay_06`, AWS Free Plan) · **Region** ap-south-1 ·
**Deployed** 2026-09-08 · engine: RDS **MySQL 8.0.43** `db.t4g.micro`.

`cdk deploy --all` → all 7 stacks `CREATE_COMPLETE` (546 s).

| | Resource | Result |
|---|----------|--------|
| API | `https://jr6j0jt9y6.execute-api.ap-south-1.amazonaws.com/prod/` | 200 on `/inventory` |
| RDS | `sentinelcommerce-datastack-sentineldbcd10063e-...:3306` | `available` |
| Table | `SentinelCommerce-DataStack-SentinelTable15FE6C31-YFBAY1REGQ94` | seeded 4 products |
| Demo SG | `sg-0e2c5559d5cd9834e` | attached to nothing |

`scripts/demo_check.sh` → **ALL CHECKS PASSED** (10/10).

## Act-by-Act evidence

### Act 2 — real-time event pipeline ✓
`POST /cart {sku:SC-HUB-03, qty:3}` drove stock 6 → 3 (below threshold 5).
`stream_processor` log:
```
{"event": "low_stock_published", "alert": "LOW_STOCK", "sku": "SC-HUB-03", "stock": 3, "threshold": 5}
```
DynamoDB Stream → Lambda → SNS `sentinelcommerce-inventory-alerts` published.
(Email delivery pending: the SNS subscription is still `PendingConfirmation`
— click the confirmation email.)

### Act 3 — attack blocked by the $0 REQUEST authorizer ✓
* SQLi in path: `GET /inventory/1' OR '1'='1` → **HTTP 403**
  ```
  {"event": "request_blocked", "reason": "signature:'\\s*(or|and)\\s+'?\\d", "target": "/inventory/1' OR '1'='1"}
  ```
* Rate limit: parallel burst of 150 → **128 × 403** once the per-IP DynamoDB
  counter crossed 100/5-min:
  ```
  {"event": "request_blocked", "reason": "rate_limit:227/100", "ip": "106.195.41.22"}
  ```
* Known gap (documented): API Gateway does not pass the request **body** to a
  REQUEST authorizer, so SQLi in a JSON body is not inspected — path/query
  only. The RUNBOOK's Act 3 payload is in the URL.

### Act 4 — self-healing governance ✓
```
$ aws ec2 authorize-security-group-ingress --group-id sg-0e2c5559d5cd9834e --protocol tcp --port 22 --cidr 0.0.0.0/0
True
$ aws lambda invoke --function-name sentinelcommerce-security-group-watchdog --payload '{}' -
{"revoked": 1, "sg_id": "sg-0e2c5559d5cd9834e", "rules_removed": [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}
$ aws ec2 describe-security-groups --group-ids sg-0e2c5559d5cd9834e --query 'SecurityGroups[0].IpPermissions'
[]                     # rule revoked automatically
```
watchdog log: `{"event": "watchdog_remediated", "auto_remediation": "REVOKED_UNRESTRICTED_SSH", ...}`.
The 5-minute EventBridge schedule also fired on its own (`watchdog_clean`
entries) — both on-demand and scheduled paths verified.

### Act 1 — resilience (manual DR) ✓ (see RUNBOOK)
Read replica `sentinelcommerce-replica` created from the primary, then
`promote-read-replica` → standalone primary. (Manual DR, not automatic
Multi-AZ HA — deliberate $0 tradeoff, explained live.)

### Act 5 — cost / budget ✓
`SentinelCommerce-Monthly` budget = $10 USD, 80% actual / 100% forecast.
Cost Explorer for the `Project=SentinelCommerce` tag: ~$0 (new-tag data
lags ~24 h). Every resource sits in a free tier — see [AUDIT.md](AUDIT.md)
for which tiers are permanent vs 12-month.

## Post-deploy fixes applied during the run (committed)

| Commit | Fix |
|--------|-----|
| `fix(data): pin MySQL engine to 8.0.43` | `8.0.39` is not offered in ap-south-1 |
| `fix(data): enable 1-day automated backups` | read replicas require automated backups; still $0 (free-tier backup storage) |

## Teardown

`cdk destroy --all --force` + delete `sentinelcommerce-replica` +
`aws ssm delete-parameter --name /sentinelcommerce/db-password`. See
[TEARDOWN.md](TEARDOWN.md).
