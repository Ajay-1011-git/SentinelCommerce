"""ObservabilityStack - MissionControl dashboard (free-tier only).

Zero-cost refactor:
  * CloudTrail-Logs metric filter + alarm: REMOVED (the Trail it depended
    on is gone). Security-group changes are now surfaced by the
    `security_group_watchdog` Lambda's own logs + the alert it publishes.
  * WAF dashboard widgets: REPLACED with the REQUEST authorizer's block
    count (Logs Insights) and the watchdog's invocations / errors.
  * RDS / DynamoDB / Lambda widgets: kept.
  * One dashboard, a handful of widgets, zero custom metrics, zero alarms -
    inside the CloudWatch free tier (3 dashboards, 10 alarms).
"""
from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_rds as rds
from constructs import Construct


class ObservabilityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        db_instance: rds.IDatabaseInstance,
        dynamo_table: dynamodb.ITableV2,
        lambda_functions: dict,
        authorizer_fn: lambda_.IFunction,
        watchdog_fn: lambda_.IFunction,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        dash = cw.Dashboard(
            self,
            "MissionControl",
            dashboard_name="SentinelCommerce-MissionControl",
        )

        def rds_metric(name):
            return cw.Metric(
                namespace="AWS/RDS",
                metric_name=name,
                dimensions_map={
                    "DBInstanceIdentifier": db_instance.instance_identifier
                },
                statistic="Average",
                period=Duration.minutes(1),
            )

        dash.add_widgets(
            cw.GraphWidget(
                title="RDS CPU %",
                left=[rds_metric("CPUUtilization")],
                width=12,
            ),
            cw.GraphWidget(
                title="RDS connections / freeable memory",
                left=[rds_metric("DatabaseConnections")],
                right=[rds_metric("FreeableMemory")],
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
                title="Edge authorizer: blocked vs allowed (invocations)",
                left=[
                    authorizer_fn.metric_invocations(
                        period=Duration.minutes(1), label="authorizer invocations"
                    ),
                    authorizer_fn.metric_errors(
                        period=Duration.minutes(1), label="authorizer errors"
                    ),
                ],
                width=12,
            ),
        )

        error_metrics, p99_metrics = [], []
        for name, fn in sorted(lambda_functions.items()):
            error_metrics.append(
                fn.metric_errors(period=Duration.minutes(1), label=f"{name} errors")
            )
            p99_metrics.append(
                fn.metric_duration(
                    statistic="p99", period=Duration.minutes(1), label=f"{name} p99"
                )
            )
        dash.add_widgets(
            cw.GraphWidget(title="Lambda errors", left=error_metrics, width=12),
            cw.GraphWidget(title="Lambda p99 duration", left=p99_metrics, width=12),
        )

        dash.add_widgets(
            cw.GraphWidget(
                title="security_group_watchdog: invocations / errors",
                left=[
                    watchdog_fn.metric_invocations(period=Duration.minutes(5)),
                    watchdog_fn.metric_errors(period=Duration.minutes(5)),
                ],
                width=12,
            ),
            cw.LogQueryWidget(
                title="Recent auto-remediation events (watchdog log)",
                log_group_names=[f"/aws/lambda/{watchdog_fn.function_name}"],
                view=cw.LogQueryVisualizationType.TABLE,
                query_lines=[
                    "fields @timestamp, event, sg_id, auto_remediation",
                    'filter event in ["watchdog_remediated", "watchdog_clean"]',
                    "sort @timestamp desc",
                    "limit 20",
                ],
                width=12,
            ),
        )

        dash.add_widgets(
            cw.LogQueryWidget(
                title="Recent blocked requests (edge authorizer log)",
                log_group_names=[f"/aws/lambda/{authorizer_fn.function_name}"],
                view=cw.LogQueryVisualizationType.TABLE,
                query_lines=[
                    "fields @timestamp, reason, ip, target",
                    'filter event = "request_blocked"',
                    "sort @timestamp desc",
                    "limit 20",
                ],
                width=24,
            )
        )

        self.dashboard = dash
