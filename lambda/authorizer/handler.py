"""request_authorizer - the $0 replacement for AWS WAF.

API Gateway REQUEST authorizer. Runs before every route. Two checks:

  1. Signature match - a small set of SQLi / XSS regexes tested against the
     decoded path, query string and a few headers. (API Gateway does NOT
     pass the request body to an authorizer, so body inspection is not
     possible here - path/query is where the Act 3 payload lives anyway.)
  2. Per-IP fixed-window rate limit - 100 requests / 5 min, counted in the
     existing DynamoDB table (PK="RL#<ip>", SK="WINDOW#<epoch/300>"), with
     a TTL so the counters self-expire. No second table, no cost.

Any Deny is logged as structured JSON and published to the alerts SNS
topic. A Deny makes API Gateway return 403 without ever invoking the
target Lambda.
"""
import json
import logging
import os
import re
import time
import urllib.parse

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "100"))
RATE_WINDOW = int(os.environ.get("RATE_WINDOW_SECONDS", "300"))
ALERT_TOPIC_ARN = os.environ["ALERT_TOPIC_ARN"]

_ddb = boto3.client("dynamodb")
_sns = boto3.client("sns")

# Roughly the coverage of AWSManagedRulesCommonRuleSet + SQLiRuleSet for
# the parts a demo exercises. Case-insensitive.
_SIGNATURES = [
    re.compile(p, re.I)
    for p in [
        r"'\s*(or|and)\s+'?\d",          # ' OR '1'='1 , ' or 1=1
        r"\b(union\s+select|select\s+.*\s+from|insert\s+into|drop\s+table)\b",
        r"\b(sleep|benchmark|pg_sleep)\s*\(",
        r"(;|\|\||&&)\s*(cat|ls|whoami|curl|wget)\b",  # command injection
        r"<\s*script\b|javascript:|onerror\s*=|onload\s*=",  # XSS
        r"\.\./\.\./",                    # path traversal
    ]
]


def _deny(reason, ip, method_arn, target):
    _log("request_blocked", reason=reason, ip=ip, target=target)
    try:
        _sns.publish(
            TopicArn=ALERT_TOPIC_ARN,
            Subject="SentinelCommerce request blocked",
            Message=json.dumps({"blocked": reason, "ip": ip, "target": target}),
        )
    except Exception as exc:  # noqa: BLE001 - never fail-closed on the alert
        _log("alert_publish_failed", error=str(exc))
    return _policy("Deny", method_arn, context={"reason": reason})


def _allow(method_arn):
    return _policy("Allow", method_arn)


def _policy(effect, method_arn, context=None):
    # Authorize the whole API/stage so the cached decision covers every route.
    parts = method_arn.split(":")
    api_gw_arn = parts[5].split("/")
    resource = f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}:{parts[4]}:{api_gw_arn[0]}/{api_gw_arn[1]}/*"
    doc = {
        "principalId": "sentinel-authorizer",
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}
            ],
        },
    }
    if context:
        doc["context"] = context
    return doc


def _log(event, **fields):
    logger.info(json.dumps({"event": event, **fields}))


def _rate_exceeded(ip):
    window = int(time.time() // RATE_WINDOW)
    try:
        resp = _ddb.update_item(
            TableName=TABLE_NAME,
            Key={"PK": {"S": f"RL#{ip}"}, "SK": {"S": f"WINDOW#{window}"}},
            UpdateExpression="ADD cnt :one SET expireAt = :ttl",
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":ttl": {"N": str((window + 2) * RATE_WINDOW)},
            },
            ReturnValues="UPDATED_NEW",
        )
        count = int(resp["Attributes"]["cnt"]["N"])
        return count > RATE_LIMIT, count
    except Exception as exc:  # noqa: BLE001 - fail open, log it
        _log("rate_check_failed", error=str(exc))
        return False, 0


def handler(event, context):
    method_arn = event["methodArn"]
    ip = (
        event.get("requestContext", {}).get("identity", {}).get("sourceIp")
        or event.get("headers", {}).get("X-Forwarded-For", "unknown").split(",")[0].strip()
    )
    path = urllib.parse.unquote_plus(event.get("path", "") or "")
    qs = event.get("queryStringParameters") or {}
    headers = event.get("headers") or {}
    haystack = " ".join(
        [path]
        + [f"{k}={v}" for k, v in qs.items()]
        + [headers.get(h, "") for h in ("User-Agent", "Referer", "X-Query")]
    )
    haystack = urllib.parse.unquote_plus(haystack)

    for sig in _SIGNATURES:
        if sig.search(haystack):
            return _deny(f"signature:{sig.pattern[:40]}", ip, method_arn, path)

    exceeded, count = _rate_exceeded(ip)
    if exceeded:
        return _deny(f"rate_limit:{count}/{RATE_LIMIT}", ip, method_arn, path)

    _log("request_allowed", ip=ip, target=path, rate_count=count)
    return _allow(method_arn)
