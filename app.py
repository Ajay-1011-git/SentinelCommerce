#!/usr/bin/env python3
"""SentinelCommerce CDK application - zero-cost edition.

One CDK App, seven Stack classes, deployed together with `cdk deploy --all`.
Dependency order CDK resolves from the references below:

    NetworkStack -> DataStack -> SecurityStack -> ComputeStack
      -> ObservabilityStack -> CostStack
    GovernanceStack (independent; ComputeStack depends on it for the
                     low-stock SSM parameter)

Every resource is chosen to sit inside a free tier at course-demo scale.
The one caveat: the RDS free tier is 12-month promotional, not permanent
(see stacks/data_stack.py and README). Region/account come from CDK
context / environment only - never hardcoded.
"""
import os

import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.data_stack import DataStack, DB_USERNAME
from stacks.security_stack import SecurityStack
from stacks.compute_stack import ComputeStack
from stacks.governance_stack import GovernanceStack
from stacks.observability_stack import ObservabilityStack
from stacks.cost_stack import CostStack

app = cdk.App()


def ctx(key: str, default):
    return app.node.try_get_context(f"sentinelcommerce:{key}") or default


region = ctx("region", None) or os.environ.get("CDK_DEFAULT_REGION") or "ap-south-1"
account = os.environ.get("CDK_DEFAULT_ACCOUNT")
env = cdk.Environment(account=account, region=region)

notification_email = ctx("notification_email", "ajaym556677@gmail.com")
budget_amount = float(ctx("budget_amount", "10"))
common = dict(env=env)

network = NetworkStack(app, "SentinelCommerce-NetworkStack", **common)

data = DataStack(
    app, "SentinelCommerce-DataStack", vpc=network.vpc, **common
)

security = SecurityStack(
    app, "SentinelCommerce-SecurityStack", vpc=network.vpc, **common
)

governance = GovernanceStack(app, "SentinelCommerce-GovernanceStack", **common)

compute = ComputeStack(
    app,
    "SentinelCommerce-ComputeStack",
    vpc=network.vpc,
    db_instance=data.db_instance,
    db_username=DB_USERNAME,
    dynamo_table=data.dynamo_table,
    demo_security_group=security.demo_remediation_sg,
    **common,
)
# stream_processor reads the low-stock SSM parameter at runtime.
compute.add_dependency(governance)

observability = ObservabilityStack(
    app,
    "SentinelCommerce-ObservabilityStack",
    db_instance=data.db_instance,
    dynamo_table=data.dynamo_table,
    lambda_functions=compute.lambda_functions,
    authorizer_fn=compute.authorizer_fn,
    watchdog_fn=compute.watchdog_fn,
    **common,
)

CostStack(
    app,
    "SentinelCommerce-CostStack",
    notification_email=notification_email,
    budget_amount=budget_amount,
    inventory_alerts_topic=compute.inventory_alerts_topic,
    ops_alerts_topic=compute.alerts_topic,
    **common,
)

cdk.Tags.of(app).add("Project", "SentinelCommerce")
cdk.Tags.of(app).add("Environment", "demo")

app.synth()
