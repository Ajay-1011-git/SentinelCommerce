#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# demo.sh - the single driver for the SentinelCommerce live demo.
#
#   ./scripts/demo.sh status       what's deployed, endpoints, live counters
#   ./scripts/demo.sh check        read-only smoke test (expect 10/10 PASS)
#   ./scripts/demo.sh seed         load demo products + 2 orders
#   ./scripts/demo.sh replica      create the Act 1 read replica (pre-demo)
#   ./scripts/demo.sh reset        reset rate-limit counter + restock  <-- run between rehearsals
#
#   ./scripts/demo.sh act2         Real-time inventory pipeline
#   ./scripts/demo.sh act3-sqli    SQL-injection blocked
#   ./scripts/demo.sh act3-rate    Rate limit blocked
#   ./scripts/demo.sh act4         Self-healing governance
#   ./scripts/demo.sh act5         Cost governance
#   ./scripts/demo.sh trail        CloudTrail proof for Act 4
#   ./scripts/demo.sh act1         Promote read replica  *** IRREVERSIBLE ***
# ---------------------------------------------------------------------------
set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-sentinelcommerce-agent}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; C=$'\033[36m'; N=$'\033[0m'
hdr() { echo; echo "${B}${C}==============================================================${N}"; echo "${B}${C} $* ${N}"; echo "${B}${C}==============================================================${N}"; }
step() { echo; echo "${B}${Y}-> $*${N}"; }
ok()   { echo "${G}   $*${N}"; }
bad()  { echo "${R}   $*${N}"; }

cfn_out() { aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue|[0]" --output text 2>/dev/null; }

discover() {
  API=$(aws cloudformation describe-stacks --stack-name SentinelCommerce-ComputeStack \
        --query "Stacks[0].Outputs[?contains(OutputKey,'Endpoint')].OutputValue|[0]" --output text 2>/dev/null)
  API="${API%/}"
  TABLE=$(cfn_out SentinelCommerce-DataStack TableName)
  DBID=$(cfn_out SentinelCommerce-DataStack DbInstanceId)
  SG=$(aws ec2 describe-security-groups \
        --filters "Name=tag:Project,Values=SentinelCommerce" \
                  "Name=description,Values=*demo-remediation-target*" \
        --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
  REPLICA=sentinelcommerce-replica
  SKU=SC-HUB-03
}

ddb_key() { printf '{"PK":{"S":"%s"},"SK":{"S":"%s"}}' "$1" "$2"; }

stock() { # $1 = sku
  aws dynamodb get-item --table-name "$TABLE" \
    --key "$(ddb_key "PRODUCT#$1" INVENTORY)" \
    --query 'Item.stock.N' --output text 2>/dev/null
}

myip() { curl -s --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '\r\n'; }

now_ms() { echo $(( $(date +%s) * 1000 )); }

# poll a log group for a pattern, up to N seconds - far more reliable live than a fixed sleep
wait_log() { # $1=loggroup $2=pattern $3=seconds $4=start-time-ms (optional)
  local lg="$1" pat="$2" secs="${3:-60}" start="${4:-}" out
  [[ -z "$start" ]] && start=$(( ($(date +%s) - 180) * 1000 ))
  for ((i=0; i<secs; i+=5)); do
    out=$(aws logs filter-log-events --log-group-name "$lg" --start-time "$start" \
          --filter-pattern "$pat" --query 'events[].message' --output text 2>/dev/null)
    if [[ -n "${out// /}" && "$out" != "None" ]]; then echo "$out"; return 0; fi
    sleep 5
  done
  return 1
}

rate_counter() {
  local w c; w=$(( $(date +%s) / 300 ))
  c=$(aws dynamodb get-item --table-name "$TABLE" \
        --key "$(ddb_key "RL#$(myip)" "WINDOW#$w")" \
        --query 'Item.cnt.N' --output text 2>/dev/null)
  [[ -z "$c" || "$c" == "None" ]] && echo 0 || echo "$c"
}

cmd="${1:-status}"
discover

case "$cmd" in

status)
  hdr "SentinelCommerce - STATUS"
  echo "Account : $(aws sts get-caller-identity --query Account --output text)"
  echo "Identity: $(aws sts get-caller-identity --query Arn --output text)"
  echo "Region  : $AWS_REGION"
  step "Stacks"
  aws cloudformation list-stacks \
    --query "StackSummaries[?starts_with(StackName,'SentinelCommerce') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" --output table
  step "Key resources"
  echo "  API endpoint : $API"
  echo "  DynamoDB     : $TABLE"
  echo "  RDS primary  : $DBID"
  echo "  Demo SG      : $SG"
  step "RDS instances"
  aws rds describe-db-instances \
    --query 'DBInstances[].{id:DBInstanceIdentifier,status:DBInstanceStatus,class:DBInstanceClass,replicaOf:ReadReplicaSourceDBInstanceIdentifier}' --output table
  step "Demo SG inbound rules (expect [] before Act 4)"
  aws ec2 describe-security-groups --group-ids "$SG" --query 'SecurityGroups[0].IpPermissions' --output json
  step "Live counters"
  echo "  Rate-limit count this 5-min window for your IP: $(rate_counter)  (limit 25)"
  echo "  Low-stock threshold: $(aws ssm get-parameter --name /sentinelcommerce/low-stock-threshold --query Parameter.Value --output text 2>/dev/null)"
  echo "  $SKU stock: $(stock $SKU)"
  ;;

check)   bash "$(dirname "$0")/demo_check.sh" ;;
seed)    SENTINEL_API_URL="$API" python "$(dirname "$0")/seed_data.py" ;;

replica)
  hdr "Creating Act 1 read replica (takes ~10-15 min)"
  aws rds create-db-instance-read-replica --db-instance-identifier "$REPLICA" \
    --source-db-instance-identifier "$DBID" --db-instance-class db.t4g.micro --no-multi-az \
    --tags Key=Project,Value=SentinelCommerce Key=Environment,Value=demo \
    --query 'DBInstance.[DBInstanceIdentifier,DBInstanceStatus]' --output text
  step "Waiting for it to become available..."
  aws rds wait db-instance-available --db-instance-identifier "$REPLICA" && ok "replica AVAILABLE"
  ;;

reset)
  hdr "RESET - run this between rehearsals and right before the real demo"
  step "Restocking $SKU to 6 (above the threshold of 5)"
  aws dynamodb update-item --table-name "$TABLE" \
    --key "$(ddb_key "PRODUCT#$SKU" INVENTORY)" \
    --update-expression "SET stock = :s" --expression-attribute-values '{":s":{"N":"6"}}' >/dev/null
  ok "stock = $(stock $SKU)"
  step "Clearing rate-limit counters for the current + previous window"
  MYIP=$(myip)
  W=$(( $(date +%s) / 300 ))
  for w in $((W-1)) $W $((W+1)); do
    aws dynamodb delete-item --table-name "$TABLE" \
      --key "$(ddb_key "RL#$MYIP" "WINDOW#$w")" >/dev/null 2>&1
  done
  ok "rate-limit counter cleared for IP $MYIP"
  step "Removing any leftover SSH rule on the demo SG"
  aws ec2 revoke-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr 0.0.0.0/0 >/dev/null 2>&1 \
    && ok "leftover SSH rule removed" || ok "no leftover SSH rule (already clean)"
  echo; ok "READY. Acts 2, 3, 4 can now be run from a clean state."
  ;;

act2)
  hdr "ACT 2 - Real-time inventory pipeline"
  step "BEFORE: current stock + threshold"
  echo "  threshold = $(aws ssm get-parameter --name /sentinelcommerce/low-stock-threshold --query Parameter.Value --output text)   (from SSM Parameter Store, not hardcoded)"
  echo "  $SKU stock = $(stock $SKU)"
  T0=$(( $(now_ms) - 3000 ))
  step "Placing an order that takes stock 6 -> 3 (crosses below the threshold)"
  echo "  POST $API/cart  {\"sku\":\"$SKU\",\"qty\":3}"
  curl -s -XPOST "$API/cart" -H 'content-type: application/json' \
    -d "{\"cart_id\":\"demo\",\"sku\":\"$SKU\",\"qty\":3}"; echo
  sleep 2
  step "AFTER: stock now"
  echo "  $SKU stock = $(stock $SKU)"
  step "Waiting for the DynamoDB Stream -> Lambda -> SNS alert (usually 5-20s)..."
  if out=$(wait_log /aws/lambda/sentinelcommerce-stream-processor "{ \$.event = \"low_stock_published\" && \$.sku = \"$SKU\" }" 90 "$T0"); then
    ok "ALERT PUBLISHED:"; echo "$out" | tail -2 | sed 's/^/     /'
  else
    bad "no alert seen yet - open the stream_processor log group in the console and refresh"
  fi
  ;;

act3-sqli)
  hdr "ACT 3 (part 1) - SQL injection blocked"
  T0=$(( $(now_ms) - 3000 ))
  step "Sending: GET $API/inventory/1' OR '1'='1"
  code=$(curl -s -o /tmp/sqli.out -w "%{http_code}" "$API/inventory/1'%20OR%20'1'='1")
  echo "  HTTP $code"; cat /tmp/sqli.out; echo
  [[ "$code" == "403" ]] && ok "BLOCKED (403) - the authorizer denied it before any handler ran" \
                         || bad "expected 403, got $code"
  step "The authorizer's own log line:"
    wait_log /aws/lambda/sentinelcommerce-request-authorizer '{ $.event = "request_blocked" }' 60 "$T0" \
    | tail -2 | sed 's/^/     /' 
  ;;

act3-rate)
  hdr "ACT 3 (part 2) - Rate limit blocked  (limit 25 per 5 min per IP)"
  step "Counter before: $(rate_counter)"
  T0=$(( $(now_ms) - 3000 ))
  step "Sending a burst of 40 requests to $API/inventory ..."
  seq 1 40 | xargs -P 8 -I{} curl -s -o /dev/null -w "%{http_code}\n" "$API/inventory" \
    | sort | uniq -c | sed 's/^/     /'
  ok "200 = allowed, 403 = blocked once the per-IP counter passed 25"
  step "Counter after: $(rate_counter)"
  step "The authorizer's block reasons:"
    wait_log /aws/lambda/sentinelcommerce-request-authorizer 'rate_limit' 60 "$T0" | tail -3 | sed 's/^/     /' 
  ;;

act4)
  hdr "ACT 4 - Self-healing governance"
  step "BEFORE: inbound rules on $SG"
  aws ec2 describe-security-groups --group-ids "$SG" --query 'SecurityGroups[0].IpPermissions' --output json
  T0=$(( $(now_ms) - 3000 ))
  step "MISCONFIGURE: opening SSH (port 22) to 0.0.0.0/0 - the whole internet"
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr 0.0.0.0/0 \
    --query 'Return' --output text
  step "Rules now (the dangerous rule is live):"
  aws ec2 describe-security-groups --group-ids "$SG" --query 'SecurityGroups[0].IpPermissions' --output json
  step "Forcing the watchdog now instead of waiting for its 5-minute schedule"
  aws lambda invoke --function-name sentinelcommerce-security-group-watchdog --payload '{}' /tmp/wd.json >/dev/null
  echo "  watchdog returned: $(cat /tmp/wd.json)"
  step "AFTER: inbound rules on $SG"
  aws ec2 describe-security-groups --group-ids "$SG" --query 'SecurityGroups[0].IpPermissions' --output json
  step "The watchdog's own log line:"
    wait_log /aws/lambda/sentinelcommerce-security-group-watchdog '{ $.event = "watchdog_remediated" }' 60 "$T0" \
    | tail -1 | sed 's/^/     /' 
  echo; ok "Rule detected and revoked automatically, with no human in the loop."
  echo "   Now run:  ./scripts/demo.sh trail     (independent CloudTrail proof)"
  ;;

trail)
  hdr "CloudTrail Event History - independent proof for Act 4"
  echo "(built-in, always-on, free 90-day history - no CloudTrail Trail resource was created)"
  echo "NOTE: CloudTrail can lag 5-15 minutes. If empty, show the Lambda log instead and re-run later."
  for ev in AuthorizeSecurityGroupIngress RevokeSecurityGroupIngress; do
    step "$ev"
    aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=$ev \
      --max-results 3 --query 'Events[].{Time:EventTime,User:Username,Event:EventName}' --output table
  done
  ;;

act5)
  hdr "ACT 5 - Cost governance"
  step "The budget (safety net)"
  aws budgets describe-budget --account-id "$(aws sts get-caller-identity --query Account --output text)" \
    --budget-name SentinelCommerce-Monthly \
    --query 'Budget.{Name:BudgetName,Limit:BudgetLimit.Amount,Unit:BudgetLimit.Unit,TimeUnit:TimeUnit}' --output table
  step "Month-to-date spend tagged Project=SentinelCommerce"
  aws ce get-cost-and-usage --time-period Start="$(date -u '+%Y-%m-01')",End="$(date -u '+%Y-%m-%d')" \
    --granularity MONTHLY --metrics UnblendedCost \
    --filter '{"Tags":{"Key":"Project","Values":["SentinelCommerce"]}}' \
    --query 'ResultsByTime[0].Total.UnblendedCost' --output table 2>/dev/null \
    || echo "  (Cost Explorer lags ~24h for a newly-created tag - show the Billing console instead)"
  step "Everything running, by service"
  echo "  RDS       : 1 x db.t4g.micro, single-AZ, 20GB   -> free tier (750 hrs/mo)"
  echo "  Lambda    : 7 functions                          -> free tier (1M req/mo, permanent)"
  echo "  DynamoDB  : 1 on-demand table                    -> free tier (25GB storage, permanent)"
  echo "  API GW    : 1 REST API                           -> free tier (1M calls/mo)"
  echo "  SNS/SSM/EventBridge/CloudWatch/Budgets           -> permanent free tiers"
  echo "  NAT Gateway / Aurora / WAF / Config / KMS CMK / Secrets Manager -> NONE (deliberately removed)"
  ;;

act1)
  hdr "ACT 1 - Resilience: promote the read replica"
  echo "${R}${B}*** THIS IS IRREVERSIBLE. The replica becomes a standalone database ***${N}"
  step "BEFORE"
  aws rds describe-db-instances --db-instance-identifier "$REPLICA" \
    --query 'DBInstances[0].{id:DBInstanceIdentifier,status:DBInstanceStatus,replicaOf:ReadReplicaSourceDBInstanceIdentifier}' --output table
  echo; read -r -p "${B}Type PROMOTE to continue: ${N}" ans
  [[ "$ans" == "PROMOTE" ]] || { bad "aborted"; exit 1; }
  step "Promoting..."
  aws rds promote-read-replica --db-instance-identifier "$REPLICA" \
    --query 'DBInstance.[DBInstanceIdentifier,DBInstanceStatus]' --output text
  step "Waiting for promotion to settle (~2-4 min)..."
  sleep 45
  aws rds wait db-instance-available --db-instance-identifier "$REPLICA"
  step "AFTER  (replicaOf should now be None = standalone primary)"
  aws rds describe-db-instances --db-instance-identifier "$REPLICA" \
    --query 'DBInstances[0].{id:DBInstanceIdentifier,status:DBInstanceStatus,replicaOf:ReadReplicaSourceDBInstanceIdentifier}' --output table
  ok "Promotion complete - manual DR action succeeded."
  ;;

*) sed -n '2,22p' "$0"; exit 1 ;;
esac
