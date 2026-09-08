"""security_group_watchdog - the $0 replacement for AWS Config + SSM Automation.

Runs outside any VPC. Triggered every 5 minutes by an EventBridge rule
(EventBridge scheduled rules + same-account Lambda delivery are free), and
also invokable on demand (`aws lambda invoke`) so the demo can force it
instantly instead of waiting for the schedule.

Logic:
  1. ec2:DescribeSecurityGroups on the one demo security group.
  2. If any inbound rule allows 0.0.0.0/0 (or ::/0) on port 22 ->
     ec2:RevokeSecurityGroupIngress to remove exactly that rule.
  3. sns:Publish to the alerts topic describing the fix.

IAM role (in ComputeStack) is scoped to those three actions on that one
security group only.
"""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SG_ID = os.environ["DEMO_SG_ID"]
ALERT_TOPIC_ARN = os.environ["ALERT_TOPIC_ARN"]

_ec2 = boto3.client("ec2")
_sns = boto3.client("sns")

_OPEN_CIDRS = {"0.0.0.0/0", "::/0"}


def _log(event, **fields):
    logger.info(json.dumps({"event": event, **fields}))


def _offending_permissions(sg):
    bad = []
    for perm in sg.get("IpPermissions", []):
        from_p, to_p = perm.get("FromPort"), perm.get("ToPort")
        if from_p is None or not (from_p <= 22 <= to_p):
            continue
        v4 = [r for r in perm.get("IpRanges", []) if r.get("CidrIp") in _OPEN_CIDRS]
        v6 = [r for r in perm.get("Ipv6Ranges", []) if r.get("CidrIpv6") in _OPEN_CIDRS]
        if not v4 and not v6:
            continue
        rule = {
            "IpProtocol": perm.get("IpProtocol", "tcp"),
            "FromPort": from_p,
            "ToPort": to_p,
        }
        if v4:
            rule["IpRanges"] = v4
        if v6:
            rule["Ipv6Ranges"] = v6
        bad.append(rule)
    return bad


def handler(event, context):
    sg = _ec2.describe_security_groups(GroupIds=[SG_ID])["SecurityGroups"][0]
    bad = _offending_permissions(sg)
    if not bad:
        _log("watchdog_clean", sg_id=SG_ID)
        return {"revoked": 0, "sg_id": SG_ID}

    _ec2.revoke_security_group_ingress(GroupId=SG_ID, IpPermissions=bad)
    msg = {
        "auto_remediation": "REVOKED_UNRESTRICTED_SSH",
        "sg_id": SG_ID,
        "rules_removed": bad,
    }
    _log("watchdog_remediated", **msg)
    _sns.publish(
        TopicArn=ALERT_TOPIC_ARN,
        Subject="SentinelCommerce auto-remediation: unrestricted SSH revoked",
        Message=json.dumps(msg, default=str),
    )
    return {"revoked": len(bad), "sg_id": SG_ID, "rules_removed": bad}
