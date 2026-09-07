"""ObservabilityStack - MissionControl dashboard, CloudTrail, and an
AuthorizeSecurityGroupIngress alarm.

Module 5 supporting evidence: one screen the professor can watch during
all five acts.
"""
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudtrail as cloudtrail
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from constructs import Construct


class ObservabilityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        aurora_cluster: rds.IDatabaseCluster,
        dynamo_table: dynamodb.ITableV2,
        lambda_functions: dict,
        web_acl_name: str,
        web_acl_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alerts_topic = sns.Topic(
            self,
            "OpsAlertsTopic",
            topic_name="sentinelcommerce-alerts",
        )

        # --- CloudTrail: single trail, single-region --------------------
        # Single-region is fine for this scope - the whole demo lives in
        # ap-south-1 and a multi-region trail would only add S3 cost.
        trail_bucket = s3.Bucket(
            self,
            "TrailBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(14))],
            auto_delete_objects=True,  # demo - cdk destroy must clean this
            removal_policy=RemovalPolicy.DESTROY,
        )
        trail_log_group = logs.LogGroup(
            self,
            "TrailLogGroup",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        trail = cloudtrail.Trail(
            self,
            "SentinelTrail",
            bucket=trail_bucket,
            is_multi_region_trail=False,
            send_to_cloud_watch_logs=True,
            cloud_watch_log_group=trail_log_group,
        )

        # Metric filter: any AuthorizeSecurityGroupIngress call (this is what
        # the governance demo triggers when port 22 is opened).
        authorize_metric = logs.MetricFilter(
            self,
            "AuthorizeSgIngressFilter",
            log_group=trail_log_group,
            metric_namespace="SentinelCommerce",
            metric_name="AuthorizeSecurityGroupIngress",
            filter_pattern=logs.FilterPattern.literal(
                '{ $.eventName = "AuthorizeSecurityGroupIngress" }'
            ),
            metric_value="1",
            default_value=0,
        )
        cw.Alarm(
            self,
            "AuthorizeSgIngressAlarm",
            metric=authorize_metric.metric(
                statistic="Sum", period=Duration.minutes(1)
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="A security group ingress rule was authorized",
        ).add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        # --- MissionControl dashboard --------------------------------
        dash = cw.Dashboard(
            self,
            "MissionControl",
            dashboard_name="SentinelCommerce-MissionControl",
        )

        def aurora_metric(name, role):
            return cw.Metric(
                namespace="AWS/RDS",
                metric_name=name,
                dimensions_map={"DBClusterIdentifier": aurora_cluster.cluster_identifier, "Role": role},
                statistic="Average",
                period=Duration.minutes(1),
            )

        dash.add_widgets(
            cw.GraphWidget(
                title="Aurora CPU (writer vs reader)",
                left=[aurora_metric("CPUUtilization", "WRITER"), aurora_metric("CPUUtilization", "READER")],
                width=12,
            ),
            cw.GraphWidget(
                title="Aurora DB connections (writer vs reader)",
                left=[aurora_metric("DatabaseConnections", "WRITER"), aurora_metric("DatabaseConnections", "READER")],
                width=12,
            ),
        )
        dash.add_widgets(
            cw.GraphWidget(
                title="DynamoDB consumed capacity",
                left=[
                    dynamo_table.metric_consumed_read_capacity_units(),
                    dynamo_table.metric_consumed_write_capacity_units(),
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="WAF allowed vs blocked",
                left=[
                    cw.Metric(
                        namespace="AWS/WAFV2",
                        metric_name="AllowedRequests",
                        dimensions_map={"WebACL": web_acl_name, "Region": self.region, "Rule": "ALL"},
                        statistic="Sum",
                        period=Duration.minutes(1),
                    ),
                    cw.Metric(
                        namespace="AWS/WAFV2",
                        metric_name="BlockedRequests",
                        dimensions_map={"WebACL": web_acl_name, "Region": self.region, "Rule": "ALL"},
                        statistic="Sum",
                        period=Duration.minutes(1),
                    ),
                ],
                width=12,
            ),
        )

        error_metrics, p99_metrics = [], []
        for name, fn in sorted(lambda_functions.items()):
            error_metrics.append(fn.metric_errors(period=Duration.minutes(1), label=f"{name} errors"))
            p99_metrics.append(fn.metric_duration(statistic="p99", period=Duration.minutes(1), label=f"{name} p99"))
        dash.add_widgets(
            cw.GraphWidget(title="Lambda errors", left=error_metrics, width=12),
            cw.GraphWidget(title="Lambda p99 duration", left=p99_metrics, width=12),
        )

        dash.add_widgets(
            cw.LogQueryWidget(
                title="Recent auto-remediation events (CloudTrail: Revoke/Authorize SG ingress)",
                log_group_names=[trail_log_group.log_group_name],
                view=cw.LogQueryVisualizationType.TABLE,
                query_lines=[
                    "fields @timestamp, userIdentity.arn as who, eventName, "
                    "requestParameters.groupId as sg",
                    'filter eventName in ["RevokeSecurityGroupIngress", "AuthorizeSecurityGroupIngress"]',
                    "sort @timestamp desc",
                    "limit 20",
                ],
                width=24,
            )
        )

        self.dashboard = dash
