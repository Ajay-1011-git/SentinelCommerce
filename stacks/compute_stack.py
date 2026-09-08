"""ComputeStack - REST API Gateway + the application Lambda functions.

Also owns:
  * the sentinelcommerce-inventory-alerts SNS topic (Module 2 sink), and
  * the WAF WebACL association to the REST API stage (the WebACL itself is
    built in SecurityStack; doing the association here breaks what would
    otherwise be a circular stack dependency).
"""
import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_ssm as ssm
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

LAMBDA_ROOT = os.path.join(os.path.dirname(__file__), "..", "lambda")
LAYER_ROOT = os.path.join(os.path.dirname(__file__), "..", "layers")
RUNTIME = lambda_.Runtime.PYTHON_3_13


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        kms_key: kms.IKey,
        web_acl_arn: str,
        aurora_cluster: rds.IDatabaseCluster,
        aurora_secret: secretsmanager.ISecret,
        dynamo_table: dynamodb.ITableV2,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lambda_functions: dict[str, lambda_.Function] = {}

        # Low-stock threshold lives in SSM Parameter Store (see GovernanceStack
        # for the authoritative definition). We reference it by name here so
        # the value is never baked into a Lambda env var.
        low_stock_param_name = "/sentinelcommerce/low-stock-threshold"

        # SNS-managed encryption (not the app CMK): a CMK on the topic would
        # require a cross-stack kms grant for every publisher/subscriber and
        # recreate the Security<->Compute dependency cycle. The "one key,
        # three layers" statement is specifically about Aurora + DynamoDB +
        # the DB secret; SNS is out of that scope.
        self.inventory_alerts_topic = sns.Topic(
            self,
            "InventoryAlertsTopic",
            topic_name="sentinelcommerce-inventory-alerts",
        )

        pg_layer = lambda_.LayerVersion(
            self,
            "Pg8000Layer",
            code=lambda_.Code.from_asset(os.path.join(LAYER_ROOT, "pg8000")),
            compatible_runtimes=[RUNTIME],
            description="Pure-Python pg8000 PostgreSQL driver for the Aurora-facing Lambdas",
        )

        vpc_subnets = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        )

        def make_fn(name, asset_dir, handler, *, env=None, layers=None, timeout=10):
            fn = lambda_.Function(
                self,
                name,
                function_name=f"sentinelcommerce-{name}",
                runtime=RUNTIME,
                handler=handler,
                code=lambda_.Code.from_asset(os.path.join(LAMBDA_ROOT, asset_dir)),
                vpc=vpc,
                vpc_subnets=vpc_subnets,
                timeout=Duration.seconds(timeout),
                log_retention=logs.RetentionDays.ONE_WEEK,  # cost
                environment={"LOG_LEVEL": "INFO", **(env or {})},
                layers=layers or [],
            )
            self.lambda_functions[name] = fn
            return fn

        db_env = {
            "DB_WRITER_ENDPOINT": aurora_cluster.cluster_endpoint.hostname,
            "DB_READER_ENDPOINT": aurora_cluster.cluster_read_endpoint.hostname,
            "DB_SECRET_ARN": aurora_secret.secret_arn,
            "DB_NAME": "sentinelcommerce",
        }

        # NOTE on cross-stack grants: the CMK lives in SecurityStack and the
        # Aurora cluster / DynamoDB table live in DataStack. Using the CDK
        # `grant_*` helpers here would mutate those resources' policies with a
        # reference back to these Lambda roles and create a stack dependency
        # cycle (Security/Data <-> Compute). So every permission below is an
        # explicit least-privilege identity policy on the function's own role,
        # scoped to a specific ARN - never a managed or wildcard policy.
        def allow(fn, actions, resources):
            fn.add_to_role_policy(
                iam.PolicyStatement(actions=actions, resources=resources)
            )

        key_decrypt_actions = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKey"]

        # --- Aurora-facing Lambdas ------------------------------------
        create_order = make_fn(
            "create-order", "create_order", "handler.handler",
            env=db_env, layers=[pg_layer],
        )
        get_reports = make_fn(
            "get-reports", "get_reports", "handler.handler",
            env=db_env, layers=[pg_layer],
        )
        for fn in (create_order, get_reports):
            allow(fn, ["secretsmanager:GetSecretValue"], [aurora_secret.secret_arn])
            allow(fn, key_decrypt_actions, [kms_key.key_arn])
        # Aurora's own security group (in DataStack) already permits the VPC
        # CIDR on the DB port, so Lambdas in the private subnets can connect
        # without a cross-stack SG reference.

        # --- DynamoDB-facing Lambdas --------------------------------
        get_inventory = make_fn(
            "get-inventory", "inventory", "handler.get_inventory",
            env={"TABLE_NAME": dynamo_table.table_name},
        )
        update_cart = make_fn(
            "update-cart", "inventory", "handler.update_cart",
            env={"TABLE_NAME": dynamo_table.table_name},
        )
        allow(get_inventory, ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
              [dynamo_table.table_arn])
        allow(update_cart,
              ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan",
               "dynamodb:PutItem", "dynamodb:UpdateItem"],
              [dynamo_table.table_arn])
        for fn in (get_inventory, update_cart):
            allow(fn, key_decrypt_actions, [kms_key.key_arn])

        # --- stream_processor: DynamoDB Streams -> SNS ---------------
        stream_processor = make_fn(
            "stream-processor", "stream_processor", "handler.handler",
            env={
                "ALERT_TOPIC_ARN": self.inventory_alerts_topic.topic_arn,
                "LOW_STOCK_PARAM_NAME": low_stock_param_name,
            },
        )
        # EventSourceMapping directly (not the DynamoEventSource helper): the
        # helper auto-calls table.grantStreamRead, which would grant on the
        # DataStack table + SecurityStack CMK and recreate the dependency
        # cycle. We wire the mapping and grant stream reads by hand instead.
        lambda_.EventSourceMapping(
            self,
            "StreamMapping",
            target=stream_processor,
            event_source_arn=dynamo_table.table_stream_arn,
            starting_position=lambda_.StartingPosition.LATEST,
            batch_size=10,
            retry_attempts=2,
            enabled=True,
        )
        self.inventory_alerts_topic.grant_publish(stream_processor)  # same stack
        allow(stream_processor,
              ["dynamodb:GetRecords", "dynamodb:GetShardIterator",
               "dynamodb:DescribeStream", "dynamodb:ListStreams"],
              [f"{dynamo_table.table_arn}/stream/*"])
        allow(stream_processor, key_decrypt_actions, [kms_key.key_arn])
        allow(stream_processor, ["ssm:GetParameter"],
              [f"arn:aws:ssm:{self.region}:{self.account}:parameter{low_stock_param_name}"])

        # --- REST API Gateway (regional) ---------------------------
        api = apigw.RestApi(
            self,
            "SentinelApi",
            rest_api_name="sentinelcommerce",
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=50,
                throttling_burst_limit=20,
                metrics_enabled=True,
            ),
        )
        api.root.add_resource("orders").add_method(
            "POST", apigw.LambdaIntegration(create_order)
        )
        api.root.add_resource("reports").add_method(
            "GET", apigw.LambdaIntegration(get_reports)
        )
        inv = api.root.add_resource("inventory")
        inv.add_method("GET", apigw.LambdaIntegration(get_inventory))
        inv.add_resource("{sku}").add_method(
            "GET", apigw.LambdaIntegration(get_inventory)
        )
        api.root.add_resource("cart").add_method(
            "POST", apigw.LambdaIntegration(update_cart)
        )

        self.api = api
        self.rest_api_stage_arn = api.deployment_stage.stage_arn

        # --- WAF association (WebACL built in SecurityStack) --------
        wafv2.CfnWebACLAssociation(
            self,
            "ApiWafAssociation",
            resource_arn=(
                f"arn:aws:apigateway:{self.region}::/restapis/"
                f"{api.rest_api_id}/stages/{api.deployment_stage.stage_name}"
            ),
            web_acl_arn=web_acl_arn,
        )
