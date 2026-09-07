#!/usr/bin/env bash
# predeploy_check.sh - safety gate to run BEFORE `cdk deploy`.
#
# AWS Config allows only ONE configuration recorder per account per region.
# GovernanceStack creates one, so if this account/region already has a
# recorder the deploy would fail halfway through. Halt early with a clear
# message instead.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
echo "== Pre-deploy checks (region: $REGION) =="

echo "-- caller identity --"
aws sts get-caller-identity --output table

echo "-- existing AWS Config configuration recorders --"
EXISTING=$(aws configservice describe-configuration-recorders \
  --region "$REGION" \
  --query 'ConfigurationRecorders[].name' --output text)

if [[ -n "${EXISTING// /}" ]]; then
  echo "ERROR: this account/region already has a Config configuration recorder: ${EXISTING}"
  echo "GovernanceStack would try to create a second one and fail."
  echo "Options: (a) deploy without GovernanceStack, or"
  echo "         (b) remove/reuse the existing recorder, or"
  echo "         (c) adapt GovernanceStack to reference the existing recorder."
  exit 1
fi

echo "OK: no existing configuration recorder. Safe to deploy GovernanceStack."
