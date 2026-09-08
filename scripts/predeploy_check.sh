#!/usr/bin/env bash
# predeploy_check.sh - run BEFORE `cdk deploy`.
#
# Zero-cost edition: the only hard prerequisite is the operator-created
# SSM parameter that both DataStack and the two RDS Lambdas resolve at
# deploy time. (AWS Config is no longer used, so the old
# one-configuration-recorder check is gone.)
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
echo "== Pre-deploy checks (region: $REGION) =="

echo "-- caller identity --"
aws sts get-caller-identity --output table

echo "-- /sentinelcommerce/db-password parameter --"
if aws ssm get-parameter --name /sentinelcommerce/db-password --region "$REGION" \
     --query 'Parameter.Type' --output text 2>/dev/null | grep -qE 'String|SecureString'; then
  TYPE=$(aws ssm get-parameter --name /sentinelcommerce/db-password --region "$REGION" \
         --query 'Parameter.Type' --output text)
  if [[ "$TYPE" != "String" ]]; then
    echo "WARNING: parameter type is $TYPE. The Lambda env injection uses"
    echo "{{resolve:ssm:...}} which only resolves String parameters - deploy"
    echo "of ComputeStack will fail. Recreate it as --type String."
    exit 1
  fi
  echo "OK: /sentinelcommerce/db-password exists (String)."
else
  echo "ERROR: /sentinelcommerce/db-password not found. Create it first:"
  echo "  aws ssm put-parameter --name /sentinelcommerce/db-password --type String \\"
  echo "    --value \"\$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')\" --region $REGION"
  exit 1
fi

echo "OK: safe to deploy."
