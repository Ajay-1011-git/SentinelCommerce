"""DataStack - a free-tier RDS instance + the DynamoDB single table.

Zero-cost refactor:
  * Aurora is GONE. Aurora has no free tier at any scale and (on this
    account) is blocked outright. Replaced with a standard
    `rds.DatabaseInstance`, MySQL, `db.t4g.micro` (smallest burstable
    Graviton class, free-tier eligible), `multi_az=False`,
    `allocated_storage=20` (inside the 20 GB free storage allowance),
    in the isolated subnets.
  * No customer-managed KMS key. Storage encryption uses the AWS-managed
    `aws/rds` key (no monthly charge; KMS requests stay inside the
    always-free 20,000/month allowance).
  * No `Credentials.from_generated_secret()` - that always creates a
    Secrets Manager secret ($0.40/mo). The master password comes from a
    pre-existing SSM Parameter Store parameter that the operator creates
    by hand before this stack is deployed (see RUNBOOK / README "manual
    step"). See the note below on String vs SecureString.
  * DynamoDB: unchanged design, encryption left at the default
    (AWS-owned key, free) - NOT AWS_MANAGED or CUSTOMER_MANAGED.

FREE-TIER CAVEAT (flagged per the project's safety rules): the RDS free
tier (750 hrs/mo of db.t4g.micro single-AZ + 20 GB) is a **12-month**
promotional tier, not a permanent one. It is $0 for the duration of this
course, but verify the account's free-tier status / the AWS Pricing
Calculator before a long-lived deploy. Running the primary AND a read
replica together for a whole month would also exceed the 750 hrs.

PASSWORD PARAMETER - String, not SecureString: the two RDS-facing Lambdas
run in the isolated subnets and cannot call the SSM API at runtime
without a paid VPC interface endpoint or a NAT gateway. Instead the
password is injected into their environment at *deploy* time via the
CloudFormation `{{resolve:ssm:...}}` dynamic reference, which only works
for `String` parameters. SSM `String` parameters are $0, exactly like
`SecureString` - the cost goal is met - but the value is not encrypted at
rest in SSM and is visible in the Lambda configuration. Acceptable for a
throwaway demo database in an isolated subnet; flip to IAM database
auth if that tradeoff matters.
"""
from aws_cdk import CfnOutput, Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_ssm as ssm
from constructs import Construct

DB_PASSWORD_PARAM = "/sentinelcommerce/db-password"
DB_USERNAME = "dbadmin"
DB_NAME = "sentinelcommerce"


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Master password: plaintext SSM String dynamic reference. Resolves
        # at deploy time both here and in the Lambda env vars (ComputeStack).
        db_password = ssm.StringParameter.value_for_string_parameter(
            self, DB_PASSWORD_PARAM
        )

        self.db_instance = rds.DatabaseInstance(
            self,
            "SentinelDb",
            engine=rds.DatabaseInstanceEngine.mysql(
                # Pinned to a version RDS currently offers in ap-south-1
                # (see `aws rds describe-db-engine-versions --engine mysql`).
                version=rds.MysqlEngineVersion.of("8.0.43", "8.0")
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO
            ),  # db.t4g.micro - free-tier eligible
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            multi_az=False,  # $0: single-AZ (Act 1 uses a manual read replica)
            allocated_storage=20,  # inside the 20 GB free allowance
            max_allocated_storage=None,  # no storage autoscaling
            storage_type=rds.StorageType.GP2,
            storage_encrypted=True,  # AWS-managed aws/rds key, no custom CMK
            credentials=rds.Credentials.from_password(
                DB_USERNAME,
                # SecretValue wrapping the {{resolve:ssm:...}} deploy-time token.
                SecretValue.unsafe_plain_text(db_password),
            ),
            database_name=DB_NAME,
            backup_retention=Duration.days(0),  # $0: no automated backups
            delete_automated_backups=True,
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,  # demo - not production
            publicly_accessible=False,
            cloudwatch_logs_exports=[],  # $0: no log exports
        )

        # Lambdas in the isolated subnets connect over the VPC CIDR - no
        # cross-stack security-group reference (would create a dependency
        # cycle). Nothing outside the VPC can route here anyway.
        self.db_instance.connections.allow_default_port_from(
            ec2.Peer.ipv4(vpc.vpc_cidr_block), "RDS-facing Lambdas"
        )

        # --- DynamoDB single table ------------------------------------
        self.dynamo_table = dynamodb.TableV2(
            self,
            "SentinelTable",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing=dynamodb.Billing.on_demand(),  # PAY_PER_REQUEST
            # Default encryption = AWS-owned key = free. NOT aws_managed()
            # (that key costs) and NOT customer_managed_key().
            dynamo_stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.db_endpoint = self.db_instance.db_instance_endpoint_address
        self.db_port = self.db_instance.db_instance_endpoint_port

        CfnOutput(self, "DbEndpoint", value=self.db_endpoint)
        CfnOutput(self, "DbPort", value=self.db_port)
        CfnOutput(self, "DbInstanceId", value=self.db_instance.instance_identifier)
        CfnOutput(self, "TableName", value=self.dynamo_table.table_name)
