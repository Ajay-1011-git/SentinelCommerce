#!/usr/bin/env bash
# demo_check.sh - READ-ONLY post-deploy smoke test.
#
# Confirms every demo Act's mechanism is wired. Makes no changes. Run this
# right after `cdk deploy --all` and again just before the live demo.
set -uo pipefail

REGION="${AWS_REGION:-ap-south-1}"
FAIL=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAIL=1; }

echo "== SentinelCommerce demo_check (region: $REGION) =="

# --- Act 1: Aurora cluster available with 2 members --------------------
echo "[Act 1] Aurora failover target"
CID=$(aws rds describe-db-clusters --region "$REGION" \
  --query "DBClusters[?contains(DBClusterIdentifier, 'auroracluster') || contains(DBClusterIdentifier, 'AuroraCluster')].DBClusterIdentifier | [0]" \
  --output text)
if [[ "$CID" == "None" || -z "$CID" ]]; then
  fail "Aurora cluster not found"
else
  STATUS=$(aws rds describe-db-clusters --region "$REGION" --db-cluster-identifier "$CID" \
    --query 'DBClusters[0].Status' --output text)
  MEMBERS=$(aws rds describe-db-clusters --region "$REGION" --db-cluster-identifier "$CID" \
    --query 'length(DBClusters[0].DBClusterMembers)' --output text)
  [[ "$STATUS" == "available" ]] && pass "cluster $CID status=available" || fail "cluster status=$STATUS"
  [[ "$MEMBERS" == "2" ]] && pass "cluster has 2 members (writer + reader)" || fail "cluster has $MEMBERS members"
fi

# --- Act 2: DynamoDB stream event source mapping Enabled --------------
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

# --- Act 3: WAF WebACL associated with the API stage ------------------
echo "[Act 3] Blocked web attack"
API_ID=$(aws apigateway get-rest-apis --region "$REGION" \
  --query "items[?name=='sentinelcommerce'].id | [0]" --output text)
if [[ -z "$API_ID" || "$API_ID" == "None" ]]; then
  fail "REST API 'sentinelcommerce' not found"
else
  STAGE_ARN="arn:aws:apigateway:${REGION}::/restapis/${API_ID}/stages/prod"
  WACL=$(aws wafv2 get-web-acl-for-resource --region "$REGION" \
    --resource-arn "$STAGE_ARN" --query 'WebACL.Name' --output text 2>/dev/null)
  [[ -n "$WACL" && "$WACL" != "None" ]] && pass "WAF '$WACL' associated with stage prod" || fail "no WAF on API stage"
fi

# --- Act 4: Config rule + remediation configuration attached ---------
echo "[Act 4] Self-healing governance"
RULE=$(aws configservice describe-config-rules --region "$REGION" \
  --config-rule-names sentinelcommerce-restricted-ssh \
  --query 'ConfigRules[0].ConfigRuleName' --output text 2>/dev/null)
[[ "$RULE" == "sentinelcommerce-restricted-ssh" ]] && pass "Config rule exists" || fail "Config rule missing"
REM=$(aws configservice describe-remediation-configurations --region "$REGION" \
  --config-rule-names sentinelcommerce-restricted-ssh \
  --query 'RemediationConfigurations[0].TargetId' --output text 2>/dev/null)
[[ -n "$REM" && "$REM" != "None" ]] && pass "remediation configuration attached ($REM)" || fail "no remediation configuration"

# --- Act 5: budget + dashboard exist --------------------------------
echo "[Act 5] Cost / budget discipline"
DASH=$(aws cloudwatch get-dashboard --region "$REGION" \
  --dashboard-name SentinelCommerce-MissionControl \
  --query 'DashboardName' --output text 2>/dev/null)
[[ "$DASH" == "SentinelCommerce-MissionControl" ]] && pass "MissionControl dashboard exists" || fail "dashboard missing"
ACCT=$(aws sts get-caller-identity --query Account --output text)
BUDG=$(aws budgets describe-budget --account-id "$ACCT" \
  --budget-name SentinelCommerce-Monthly --query 'Budget.BudgetName' --output text 2>/dev/null)
[[ "$BUDG" == "SentinelCommerce-Monthly" ]] && pass "monthly budget exists" || fail "budget missing"

echo
[[ "$FAIL" == "0" ]] && echo "== ALL CHECKS PASSED ==" || echo "== SOME CHECKS FAILED =="
exit $FAIL
