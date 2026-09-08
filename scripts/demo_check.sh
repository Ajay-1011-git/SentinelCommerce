#!/usr/bin/env bash
# demo_check.sh - READ-ONLY post-deploy smoke test (zero-cost edition).
# Confirms every Act's mechanism is wired. Makes no changes.
set -uo pipefail

REGION="${AWS_REGION:-ap-south-1}"
FAIL=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAIL=1; }

echo "== SentinelCommerce demo_check (region: $REGION) =="

# --- Act 1: RDS instance available -----------------------------------
echo "[Act 1] RDS instance"
DBID=$(aws rds describe-db-instances --region "$REGION" \
  --query "DBInstances[?contains(DBInstanceIdentifier,'sentineldb') || contains(DBInstanceIdentifier,'sentinelcommerce')].DBInstanceIdentifier | [0]" \
  --output text)
if [[ -z "$DBID" || "$DBID" == "None" ]]; then
  fail "RDS instance not found"
else
  STATUS=$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DBID" \
    --query 'DBInstances[0].DBInstanceStatus' --output text)
  CLASS=$(aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DBID" \
    --query 'DBInstances[0].DBInstanceClass' --output text)
  [[ "$STATUS" == "available" ]] && pass "RDS $DBID available ($CLASS)" || fail "RDS status=$STATUS"
  [[ "$CLASS" == "db.t4g.micro" || "$CLASS" == "db.t3.micro" ]] && pass "free-tier instance class" || fail "class $CLASS not free-tier"
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
