#!/usr/bin/env python3
"""SentinelCommerce CDK application.

One CDK App composed of separate Stack classes, deployed together with
`cdk deploy --all`. The intended narrative/dependency order is:

    NetworkStack -> DataStack -> SecurityStack -> ComputeStack
      -> GovernanceStack -> ObservabilityStack -> CostStack

Two hard AWS constraints force the *instantiation* order to differ slightly
from that narrative (CDK still resolves the real deploy order from the
cross-stack references below):

  * DataStack encrypts Aurora + DynamoDB + the DB secret with the customer
    managed KMS key that lives in SecurityStack, so the KMS key must exist
    before DataStack. We therefore build SecurityStack before DataStack.
  * The WAF WebACL is created in SecurityStack but must be *associated* with
    the REST API stage that ComputeStack creates. To avoid a circular
    dependency, SecurityStack only creates the WebACL and ComputeStack owns
    the CfnWebACLAssociation (it imports the WebACL ARN).

Region and account come from CDK context / environment variables only -
never hardcoded in stack code.
"""
import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.data_stack import DataStack
from stacks.security_stack import SecurityStack
from stacks.compute_stack import ComputeStack
from stacks.governance_stack import GovernanceStack
from stacks.observability_stack import ObservabilityStack
from stacks.cost_stack import CostStack

app = cdk.App()


def ctx(key: str, default: str) -> str:
    return app.node.try_get_context(f"sentinelcommerce:{key}") or default


# Account is never hardcoded: it comes from the credentials in use at synth/deploy
# time. Region prefers explicit context, then the standard CDK env var, then the
# course project's target region (ap-south-1, Mumbai).
region = ctx("region", None) or os.environ.get("CDK_DEFAULT_REGION") or "ap-south-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
env = cdk.Environment(account=account, region=region)

notification_email = ctx("notification_email", "ajaym556677@gmail.com")
budget_amount = float(ctx("budget_amount", "10"))

common = dict(env=env)

network = NetworkStack(app, "SentinelCommerce-NetworkStack", **common)

security = SecurityStack(
    app,
    "SentinelCommerce-SecurityStack",
    vpc=network.vpc,
    **common,
)

data = DataStack(
    app,
    "SentinelCommerce-DataStack",
    vpc=network.vpc,
    kms_key=security.kms_key,
    **common,
)
data.add_dependency(security)

# GovernanceStack owns the SSM Parameter Store values (incl. the low-stock
# threshold ComputeStack's stream_processor reads at runtime). ComputeStack
# references that parameter by name only (no CFN dependency) to avoid a
# cross-stack cycle; both are deployed together by `cdk deploy --all`.
governance = GovernanceStack(
    app,
    "SentinelCommerce-GovernanceStack",
    demo_security_group=security.demo_remediation_sg,
    **common,
)

compute = ComputeStack(
    app,
    "SentinelCommerce-ComputeStack",
    vpc=network.vpc,
    kms_key=security.kms_key,
    web_acl_arn=security.web_acl_arn,
    aurora_cluster=data.aurora_cluster,
    aurora_secret=data.aurora_secret,
    dynamo_table=data.dynamo_table,
    **common,
)

observability = ObservabilityStack(
    app,
    "SentinelCommerce-ObservabilityStack",
    aurora_cluster=data.aurora_cluster,
    dynamo_table=data.dynamo_table,
    lambda_functions=compute.lambda_functions,
    web_acl_name=security.web_acl_name,
    web_acl_arn=security.web_acl_arn,
    **common,
)

CostStack(
    app,
    "SentinelCommerce-CostStack",
    notification_email=notification_email,
    budget_amount=budget_amount,
    inventory_alerts_topic=compute.inventory_alerts_topic,
    ops_alerts_topic=observability.alerts_topic,
    **common,
)

# App-level tags: every resource in every stack is tagged for cost traceability
# and clean Cost Explorer / Trusted Advisor views.
cdk.Tags.of(app).add("Project", "SentinelCommerce")
cdk.Tags.of(app).add("Environment", "demo")

app.synth()
