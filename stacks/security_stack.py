"""SecurityStack - what's left of "security infrastructure" at $0.

Zero-cost refactor:
  * KMS customer-managed key: GONE ($1/mo, no free tier). Aurora/DynamoDB/
    the DB secret no longer exist or use AWS-owned keys.
  * WAFv2 WebACL: GONE ($5/mo ACL + $1/rule/mo, no free tier). Its job -
    block SQLi/XSS payloads and rate-limit abusive IPs - now runs as an
    API Gateway Lambda REQUEST authorizer, built in ComputeStack (putting
    it here would create a Security<->Compute stack dependency cycle via
    the auto-generated Lambda invoke permission). The authorizer + the
    `sentinelcommerce-alerts` SNS topic live in ComputeStack.
  * AWS Shield Standard: automatic L3/L4 DDoS protection on every AWS
    account, no charge, nothing to provision.

What remains here is the one free thing this stack still owns: the unused
`demo-remediation-target` security group. It is attached to NOTHING and
exists only so the Act 4 governance demo can open port 22 to 0.0.0.0/0 on
a group that protects no real resource - the SG guarding RDS is never
touched. Security groups are always free.
"""
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class SecurityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.demo_remediation_sg = ec2.SecurityGroup(
            self,
            "DemoRemediationTargetSg",
            vpc=vpc,
            description="demo-remediation-target - unused; Act 4 blast target only",
            allow_all_outbound=False,
        )
