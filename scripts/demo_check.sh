#!/usr/bin/env bash
# demo_check.sh - READ-ONLY post-deploy smoke test (zero-cost edition).
# Confirms every Act's mechanism is wired. Makes no changes.
set -uo pipefail

REGION="${AWS_REGION:-ap-south-1}"
FAIL=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAIL=1; }

echo "== SentinelCommerce demo_check (region: $REGION) =="

# --- Act 1: RDS primary + promotable read replica ---------------------
echo "[Act 1] RDS primary + read replica"
PRIMARY=$(aws rds describe-db-instances --region "$REGION" \
  --query "DBInstances[?ReadReplicaSourceDBInstanceIdentifier==null].DBInstanceIdentifier | [0]" --output text)
if [[ -z "$PRIMARY" || "$PRIMARY" == "None" ]]; then
  fail "RDS primary not found"
else
  PSTATUS=$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$PRIMARY" \
    --query 'DBInstances[0].DBInstanceStatus' --output text)
  PCLASS=$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$PRIMARY" \
    --query 'DBInstances[0].DBInstanceClass' --output text)
  case "$PSTATUS" in
    available)              pass "primary $PRIMARY available ($PCLASS)";;
    modifying|backing-up|configuring-enhanced-monitoring)
                            pass "primary $PRIMARY $PSTATUS (transient - settles on its own)";;
    *)                      fail "primary status=$PSTATUS";;
  esac
  [[ "$PCLASS" == "db.t4g.micro" || "$PCLASS" == "db.t3.micro" ]] && pass "free-tier instance class" || fail "class $PCLASS not free-tier"
fi
REPSTATUS=$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier sentinelcommerce-replica \
  --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null)
REPOF=$(aws rds describe-db-instances --region "$REGION" \
  --db-instance-identifier sentinelcommerce-replica \
  --query 'DBInstances[0].ReadReplicaSourceDBInstanceIdentifier' --output text 2>/dev/null)
if [[ "$REPSTATUS" == "available" && -n "$REPOF" && "$REPOF" != "None" ]]; then
  pass "read replica available and replicating (Act 1 ready)"
elif [[ "$REPSTATUS" == "creating" || "$REPSTATUS" == "modifying" || "$REPSTATUS" == "backing-up" ]]; then
  fail "read replica still $REPSTATUS - wait before demoing Act 1"
elif [[ "$REPSTATUS" == "available" ]]; then
  pass "replica exists but is ALREADY PROMOTED (standalone) - Act 1 has been used"
else
  fail "read replica missing - run: ./scripts/demo.sh replica"
fi

# --- Act 2: DynamoDB stream event source mapping Enabled -------------
echo "[Act 2] Real-time event pipeline"
SP_ARN=$(aws lambda get-function --region "$REGION" \
  --function-name sentinelcommerce-stream-processor \
  --query 'Configuration.FunctionArn' --output text 2>/dev/null)
if [[ -z "$SP_ARN" || "$SP_ARN" == "None" ]]; then
  fail "stream-processor Lambda not found"
else
  STATE=$(aws lambda list-event-source-mappings --region "$REGION" \
    --function-name "$SP_ARN" --query 'EventSourceMappings[0].State' --output text)
  [[ "$STATE" == "Enabled" ]] && pass "DynamoDB stream mapping State=Enabled" || fail "mapping State=$STATE"
fi

# --- Act 3: REQUEST authorizer attached to the API -----------------
echo "[Act 3] Edge authorizer (WAF replacement)"
API_ID=$(aws apigateway get-rest-apis --region "$REGION" \
  --query "items[?name=='sentinelcommerce'].id | [0]" --output text)
if [[ -z "$API_ID" || "$API_ID" == "None" ]]; then
  fail "REST API not found"
else
  ATYPE=$(aws apigateway get-authorizers --region "$REGION" --rest-api-id "$API_ID" \
    --query 'items[0].type' --output text)
  [[ "$ATYPE" == "REQUEST" ]] && pass "REQUEST authorizer present on API" || fail "authorizer type=$ATYPE"
  NAUTH=$(aws apigateway get-resources --region "$REGION" --rest-api-id "$API_ID" \
    --query "length(items[?resourceMethods].resourceMethods)" --output text 2>/dev/null)
  aws lambda get-function --region "$REGION" --function-name sentinelcommerce-request-authorizer \
    >/dev/null 2>&1 && pass "authorizer Lambda exists" || fail "authorizer Lambda missing"
fi

# --- Act 4: watchdog Lambda + schedule ---------------------------
echo "[Act 4] Self-healing governance"
aws lambda get-function --region "$REGION" \
  --function-name sentinelcommerce-security-group-watchdog >/dev/null 2>&1 \
  && pass "security_group_watchdog Lambda exists" || fail "watchdog Lambda missing"
RULE_STATE=$(aws events describe-rule --region "$REGION" \
  --name sentinelcommerce-sg-watchdog --query 'State' --output text 2>/dev/null)
[[ "$RULE_STATE" == "ENABLED" ]] && pass "5-min EventBridge schedule ENABLED" || fail "schedule state=$RULE_STATE"

# --- Act 5: budget + dashboard --------------------------------
echo "[Act 5] Cost / budget discipline"
DASH=$(aws cloudwatch get-dashboard --region "$REGION" \
  --dashboard-name SentinelCommerce-MissionControl --query 'DashboardName' --output text 2>/dev/null)
[[ "$DASH" == "SentinelCommerce-MissionControl" ]] && pass "MissionControl dashboard exists" || fail "dashboard missing"
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUDG=$(aws budgets describe-budget --account-id "$ACCT" \
  --budget-name SentinelCommerce-Monthly --query 'Budget.BudgetName' --output text 2>/dev/null)
[[ "$BUDG" == "SentinelCommerce-Monthly" ]] && pass "monthly budget exists" || fail "budget missing"

echo
[[ "$FAIL" == "0" ]] && echo "== ALL CHECKS PASSED ==" || echo "== SOME CHECKS FAILED =="
exit $FAIL
