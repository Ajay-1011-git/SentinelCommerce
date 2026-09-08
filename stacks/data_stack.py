"""DataStack - Aurora PostgreSQL cluster and the DynamoDB single table.

Module 1 (database failover) and Module 2 (real-time event pipeline)
originate here.

NOTE - engine choice: the target account is on the **AWS Free Plan**, which
blocks the Aurora MySQL cluster engine entirely ("The specified cluster
engine type is not available with free plan accounts. Available engine
types: [aurora-postgresql]"). The architecture is identical with Aurora
PostgreSQL - writer + reader on shared storage, the reader doubling as the
Multi-AZ failover target and the read replica - so we use aurora-postgresql
with Serverless v2 instances (lowest cost, still a real failover target).
"""
from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_rds as rds
from constructs import Construct


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        kms_key: kms.IKey,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Aurora PostgreSQL-Compatible ------------------------------
        # 1 writer + 1 reader, one instance per AZ. In Aurora the reader IS
        # both the Multi-AZ failover target AND the read replica - the same
        # mechanism serves both. There is deliberately no separate "read
        # replica" resource: promoting the reader on writer failure and
        # serving report reads from it are the same shared-storage feature.
        engine = rds.DatabaseClusterEngine.aurora_postgres(
            version=rds.AuroraPostgresEngineVersion.VER_16_6
        )

        # Serverless v2, 0.5-2 ACU. Aurora has no free-tier/micro instance;
        # Serverless v2 at min capacity is the cheapest way to keep a real
        # writer + reader pair (needed for the Act 1 failover demo) running
        # only during build/demo windows. Torn down after.
        instance_kwargs = dict(publicly_accessible=False)

        self.aurora_cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=engine,
            vpc=vpc,
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            # Isolated subnets: the database has no route to the internet.
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            writer=rds.ClusterInstance.serverless_v2("writer", **instance_kwargs),
            readers=[
                # scale_with_writer -> promotion tier 0/1, so this reader is a
                # first-class Multi-AZ failover target (Act 1).
                rds.ClusterInstance.serverless_v2(
                    "reader", scale_with_writer=True, **instance_kwargs
                )
            ],
            storage_encrypted=True,
            storage_encryption_key=kms_key,
            # from_generated_secret(): password is generated and stored in
            # Secrets Manager automatically. No password appears in code.
            credentials=rds.Credentials.from_generated_secret(
                "sentineladmin", encryption_key=kms_key
            ),
            default_database_name="sentinelcommerce",
            backup=rds.BackupProps(retention=Duration.days(1)),
            removal_policy=RemovalPolicy.DESTROY,  # demo - not production
        )

        self.aurora_secret = self.aurora_cluster.secret

        # Allow the app Lambdas (which run in this VPC's PRIVATE_WITH_EGRESS
        # subnets) to reach the DB port. We open it to the VPC CIDR here
        # rather than referencing each Lambda's security group from
        # ComputeStack - a cross-stack SG reference would create a
        # Data<->Compute dependency cycle. Nothing outside the VPC can route
        # to the isolated subnets regardless.
        self.aurora_cluster.connections.allow_default_port_from(
            ec2.Peer.ipv4(vpc.vpc_cidr_block), "App Lambdas in private subnets"
        )

        # Single-user rotation helper. Rotates every 30 days automatically;
        # a rotation can ALSO be forced on demand
        # (`aws secretsmanager rotate-secret`) - used live to show the old
        # credential fail immediately. See RUNBOOK.md.
        self.aurora_cluster.add_rotation_single_user(
            automatically_after=Duration.days(30)
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
            # Same CMK as Aurora and the DB secret - "one key, three layers".
            encryption=dynamodb.TableEncryptionV2.customer_managed_key(kms_key),
            dynamo_stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            # PITR: cheap, and a Module 4 (governance / data protection)
            # talking point in the README.
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.DESTROY,  # demo - not production
        )

        CfnOutput(self, "TableName", value=self.dynamo_table.table_name)
        CfnOutput(
            self, "AuroraClusterId", value=self.aurora_cluster.cluster_identifier
        )
        CfnOutput(self, "AuroraSecretArn", value=self.aurora_secret.secret_arn)
        CfnOutput(
            self,
            "AuroraWriterEndpoint",
            value=self.aurora_cluster.cluster_endpoint.hostname,
        )
        CfnOutput(
            self,
            "AuroraReaderEndpoint",
            value=self.aurora_cluster.cluster_read_endpoint.hostname,
        )
