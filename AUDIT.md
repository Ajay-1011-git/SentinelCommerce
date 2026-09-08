# Build Audit Log

Each task from `sentinelcommerce_claude_code_prompt.md` is implemented, then
audited (`cdk synth` + resource presence + `py_compile` + `bash -n`) and
pushed. No `cdk deploy` or resource-creating AWS CLI command has been run —
per Safety Rule 1, that waits for an explicit "deploy now".

| Task | Status | Audit |
|------|--------|-------|
| Scaffold (app.py, cdk.json, tags, env-from-context) | done | `cdk synth` OK |
| 1. NetworkStack | done | VPC + 1 NAT + 3 subnet tiers synth OK |
| 2. DataStack | done | `AWS::RDS::DBCluster` x1 (writer+reader), DynamoDB table + stream synth OK |
| 3. SecurityStack | done | KMS key x1, WAFv2 WebACL (3 rules), demo SG synth OK |
| 4. ComputeStack | done | REST API + 6 Lambdas + `EventSourceMapping` + `WebACLAssociation` synth OK |
| 5. GovernanceStack | done | `Config::ConfigRule` + `RemediationConfiguration` + `SSM::Document` synth OK |
| 6. ObservabilityStack | done | Dashboard + CloudTrail + metric-filter alarm synth OK |
| 7. CostStack | done | `Budgets::Budget` + SNS subscriptions synth OK |
| Deliverables (scripts, RUNBOOK, README, TEARDOWN) | done | `py_compile` + `bash -n` OK |

## Known verify-before-deploy items (Safety Rule 5)

* **AWS Config managed rule identifier** for restricted SSH is set to
  `INCOMING_SSH_DISABLED` in `stacks/governance_stack.py`. Confirm against
  the current AWS Config "List of Managed Rules" documentation before
  deploy — AWS has renamed rule source identifiers before.
* **WAF + REST API association** uses `CfnWebACLAssociation` with the stage
  ARN `arn:aws:apigateway:<region>::/restapis/<id>/stages/prod`. Confirm
  this is still the supported association path.
* Synth here runs **environment-agnostic** (no AWS credentials in the build
  environment). A real `cdk synth`/`deploy` with credentials will also run
  the VPC AZ context lookup.

## Billable resources (produced from `cdk synth`, for pre-deploy sanity check)

| Resource | Approx cost (ap-south-1) | Notes |
|----------|--------------------------|-------|
| Aurora PostgreSQL Serverless v2, writer+reader | ~$0.12/ACU-hr; 0.5 ACU min → ~$0.06/hr each idle | Free Plan blocks Aurora MySQL; engine switched to aurora-postgresql |
| Aurora storage + I/O | ~$0.10/GB-mo + I/O | small for demo data |
| NAT Gateway x1 | ~$0.045/hr + $0.045/GB | single NAT by design |
| Secrets Manager secret x1 | ~$0.40/mo + $0.05/10k API calls | |
| KMS CMK x1 | $1/mo + $0.03/10k requests | prorated |
| DynamoDB (PAY_PER_REQUEST) | ~$0 idle; $1.25/M writes | + PITR ~$0.20/GB-mo |
| API Gateway REST | $3.50/M calls | |
| Lambda x6 | free-tier covers demo | in-VPC, 7-day logs |
| WAFv2 WebACL + 3 rules | $5/mo ACL + $1/rule/mo + $0.60/M req | prorated |
| AWS Config | $0.003 per config item + rule evals | recorder scoped to SG only |
| CloudTrail (1st trail) | free for mgmt events; S3 storage only | 14-day expiry |
| CloudWatch dashboard | $3/mo per dashboard (first 3 free) | |
| CloudWatch alarms / logs | ~$0.10/alarm-mo + log ingest | 7–14 day retention |
| CfnBudget | first 2 budgets free | |

**Deploy and tear down the same day.** See `TEARDOWN.md`.
