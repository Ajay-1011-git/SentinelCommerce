"""ComputeStack - REST API + Lambdas + the $0 security-group watchdog.

Zero-cost refactor:
  * `create_order` / `get_reports` stay in the VPC (isolated subnets) - they
    only talk to RDS. The DB password is baked into their environment at
    deploy time via the `{{resolve:ssm:...}}` dynamic reference, so they
    make no runtime SSM call and need no VPC endpoint / NAT.
  * `get_inventory` / `update_cart` / `stream_processor` have NO `vpc=` -
    default Lambda networking (no ENI, no cost); DynamoDB / SNS / SSM are
    reached over the public AWS API.
  * `security_group_watchdog` (new, outside the VPC) replaces AWS Config +
    SSM Automation: an EventBridge rule fires it every 5 minutes (free) and
    it is also invokable on demand. It revokes any 0.0.0.0/0:22 rule on the
    demo SG and publishes the fix to the alerts topic.
  * The Lambda REQUEST authorizer (the $0 WAF replacement) + the
    `sentinelcommerce-alerts` SNS topic are built here and the authorizer
    guards every API route. No WAF, no WAF association.
"""
import os

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_sns as sns
from aws_cdk import aws_ssm as ssm
from constructs import Construct

LAMBDA_ROOT = os.path.join(os.path.dirname(__file__), "..", "lambda")
LAYER_ROOT = os.path.join(os.path.dirname(__file__), "..", "layers")
RUNTIME = lambda_.Runtime.PYTHON_3_13
DB_PASSWORD_PARAM = "/sentinelcommerce/db-password"
LOW_STOCK_PARAM = "/sentinelcommerce/low-stock-threshold"


class ComputeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        db_instance: rds.IDatabaseInstance,
        db_username: str,
        dynamo_table: dynamodb.ITableV2,
        demo_security_group: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lambda_functions: dict[str, lambda_.Function] = {}

        self.inventory_alerts_topic = sns.Topic(
            self,
            "InventoryAlertsTopic",
            topic_name="sentinelcommerce-inventory-alerts",
        )
        # The $0 WAF replacement's alert sink + the authorizer handler live
        # here (not SecurityStack) to avoid a Security<->Compute cycle from
        # the auto-generated API-Gateway->Lambda invoke permission.
        self.alerts_topic = sns.Topic(
            self, "OpsAlertsTopic", topic_name="sentinelcommerce-alerts"
        )
        authorizer_fn = lambda_.Function(
            self,
            "RequestAuthorizerFn",
            function_name="sentinelcommerce-request-authorizer",
            runtime=RUNTIME,
            handler="handler.handler",
            code=lambda_.Code.from_asset(os.path.join(LAMBDA_ROOT, "authorizer")),
            timeout=Duration.seconds(5),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "TABLE_NAME": dynamo_table.table_name,
                "RATE_LIMIT": "100",
                "RATE_WINDOW_SECONDS": "300",
                "ALERT_TOPIC_ARN": self.alerts_topic.topic_arn,
            },
        )
        authorizer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:UpdateItem", "dynamodb:GetItem"],
                resources=[dynamo_table.table_arn],
            )
        )
        self.alerts_topic.grant_publish(authorizer_fn)
        self.authorizer_fn = authorizer_fn

        pymysql_layer = lambda_.LayerVersion(
            self,
            "PyMySqlLayer",
            code=lambda_.Code.from_asset(os.path.join(LAYER_ROOT, "pymysql")),
            compatible_runtimes=[RUNTIME],
            description="Pure-Python PyMySQL for the RDS-facing Lambdas",
        )

        # DB password: deploy-time {{resolve:ssm:/sentinelcommerce/db-password}}
        db_password_token = ssm.StringParameter.value_for_string_parameter(
            self, DB_PASSWORD_PARAM
        )

        def make_fn(name, asset_dir, handler, *, env=None, layers=None,
                    in_vpc=False, timeout=10):
            kwargs_ = dict(
                function_name=f"sentinelcommerce-{name}",
                runtime=RUNTIME,
                handler=handler,
                code=lambda_.Code.from_asset(os.path.join(LAMBDA_ROOT, asset_dir)),
                timeout=Duration.seconds(timeout),
                log_retention=logs.RetentionDays.ONE_WEEK,
                environment={"LOG_LEVEL": "INFO", **(env or {})},
                layers=layers or [],
            )
            if in_vpc:
                kwargs_["vpc"] = vpc
                kwargs_["vpc_subnets"] = ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                )
            fn = lambda_.Function(self, name, **kwargs_)
            self.lambda_functions[name] = fn
            return fn

        def allow(fn, actions, resources):
            fn.add_to_role_policy(
                iam.PolicyStatement(actions=actions, resources=resources)
            )

        db_env = {
            "DB_ENDPOINT": db_instance.db_instance_endpoint_address,
            "DB_READER_ENDPOINT": db_instance.db_instance_endpoint_address,
            "DB_PORT": db_instance.db_instance_endpoint_port,
            "DB_USER": db_username,
            "DB_PASSWORD": db_password_token,
            "DB_NAME": "sentinelcommerce",
        }

        # --- RDS-facing Lambdas (in the isolated subnets) --------------
        create_order = make_fn(
            "create-order", "create_order", "handler.handler",
            env=db_env, layers=[pymysql_layer], in_vpc=True,
        )
        get_reports = make_fn(
            "get-reports", "get_reports", "handler.handler",
            env=db_env, layers=[pymysql_layer], in_vpc=True,
        )

        # --- DynamoDB Lambdas (no VPC) -------------------------------
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

        # --- stream_processor (no VPC) -> SNS ----------------------
        stream_processor = make_fn(
            "stream-processor", "stream_processor", "handler.handler",
            env={
                "ALERT_TOPIC_ARN": self.inventory_alerts_topic.topic_arn,
                "LOW_STOCK_PARAM_NAME": LOW_STOCK_PARAM,
            },
        )
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
        self.inventory_alerts_topic.grant_publish(stream_processor)
        allow(stream_processor,
              ["dynamodb:GetRecords", "dynamodb:GetShardIterator",
               "dynamodb:DescribeStream", "dynamodb:ListStreams"],
              [f"{dynamo_table.table_arn}/stream/*"])
        allow(stream_processor, ["ssm:GetParameter"],
              [f"arn:aws:ssm:{self.region}:{self.account}:parameter{LOW_STOCK_PARAM}"])

        # --- security_group_watchdog (no VPC) --------------------
        watchdog = make_fn(
            "security-group-watchdog", "sg_watchdog", "handler.handler",
            env={
                "DEMO_SG_ID": demo_security_group.security_group_id,
                "ALERT_TOPIC_ARN": self.alerts_topic.topic_arn,
            },
            timeout=15,
        )
        sg_arn = (
            f"arn:aws:ec2:{self.region}:{self.account}:security-group/"
            f"{demo_security_group.security_group_id}"
        )
        # DescribeSecurityGroups does not support resource-level scoping; the
        # write action is scoped to the one demo SG ARN.
        allow(watchdog, ["ec2:DescribeSecurityGroups"], ["*"])
        allow(watchdog, ["ec2:RevokeSecurityGroupIngress"], [sg_arn])
        self.alerts_topic.grant_publish(watchdog)

        events.Rule(
            self,
            "WatchdogSchedule",
            rule_name="sentinelcommerce-sg-watchdog",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(watchdog)],
        )

        # --- REST API + REQUEST authorizer ------------------------
        authorizer = apigw.RequestAuthorizer(
            self,
            "EdgeAuthorizer",
            handler=authorizer_fn,
            identity_sources=[apigw.IdentitySource.context("identity.sourceIp")],
            results_cache_ttl=Duration.seconds(0),  # evaluate every request
        )
        api = apigw.RestApi(
            self,
            "SentinelApi",
            rest_api_name="sentinelcommerce",
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                metrics_enabled=True,
            ),
        )

        def route(resource, verb, fn):
            resource.add_method(
                verb, apigw.LambdaIntegration(fn), authorizer=authorizer
            )

        route(api.root.add_resource("orders"), "POST", create_order)
        route(api.root.add_resource("reports"), "GET", get_reports)
        inv = api.root.add_resource("inventory")
        route(inv, "GET", get_inventory)
        route(inv.add_resource("{sku}"), "GET", get_inventory)
        route(api.root.add_resource("cart"), "POST", update_cart)

        self.api = api
        self.watchdog_fn = watchdog
