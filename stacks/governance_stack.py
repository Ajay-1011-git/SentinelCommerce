"""GovernanceStack - Systems Manager Parameter Store only.

Zero-cost refactor:
  * AWS Config (recorder, delivery channel, rules, remediation): REMOVED.
    Config has no free tier at any usage level ($0.003 per configuration
    item + per rule evaluation).
  * SSM Automation document: REMOVED. The `security_group_watchdog` Lambda
    in ComputeStack does that remediation directly via boto3.
  * CloudTrail Trail: NOT CREATED. The demo uses the account's built-in,
    always-on, always-free 90-day CloudTrail Event History
    (`aws cloudtrail lookup-events`) - no CDK resource needed.

What remains is the genuine "Systems Manager" component: Parameter Store
standard parameters, which are free.

  * `/sentinelcommerce/low-stock-threshold` - created here.
  * `/sentinelcommerce/db-password` - NOT created here; the operator makes
    that SecureString/String parameter by hand before DataStack deploys
    (see README "Manual step").
"""
from aws_cdk import Stack
from aws_cdk import aws_ssm as ssm
from constructs import Construct

LOW_STOCK_PARAM_NAME = "/sentinelcommerce/low-stock-threshold"


class GovernanceStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ssm.StringParameter(
            self,
            "LowStockThreshold",
            parameter_name=LOW_STOCK_PARAM_NAME,
            string_value="5",
            description="stream_processor publishes a low-stock alert when stock drops below this",
        )
