"""CostStack - the budget and the human-facing alert subscriptions.

Module 5 (cost / budget discipline). Unchanged by the zero-cost refactor,
and the point is now *stronger*: SentinelCommerce is architected for
genuine $0 running cost (every billable service sits inside a permanent
free tier, except RDS whose free tier is 12-month - see README). This
`CfnBudget` is purely a safety net - it fires if a free-tier limit is ever
exceeded by accident, e.g. leaving the RDS instance (and a promoted read
replica) running for a full month beyond the 750-hour allowance.

AWS Budgets: first 2 budgets per account are free. SNS: 1M publishes +
1k email notifications/month free.
"""
from aws_cdk import Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct


class CostStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        notification_email: str,
        budget_amount: float,
        inventory_alerts_topic: sns.ITopic,
        ops_alerts_topic: sns.ITopic,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Email (from CDK context, never hardcoded here) subscribed to both
        # operational topics so the demo operator actually receives the
        # low-stock and security alerts live.
        for topic in (inventory_alerts_topic, ops_alerts_topic):
            topic.add_subscription(subs.EmailSubscription(notification_email))

        # Dedicated topic for budget notifications ("both to my email via
        # SNS"). AWS Budgets must be allowed to publish to it.
        budget_topic = sns.Topic(
            self, "BudgetAlertsTopic", topic_name="sentinelcommerce-budget-alerts"
        )
        budget_topic.add_subscription(subs.EmailSubscription(notification_email))
        budget_topic.add_to_resource_policy(
            _budgets_publish_statement(budget_topic.topic_arn)
        )

        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="SentinelCommerce-Monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=budget_amount, unit="USD"
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="ACTUAL",
                        comparison_operator="GREATER_THAN",
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS", address=budget_topic.topic_arn
                        ),
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=notification_email
                        ),
                    ],
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="FORECASTED",
                        comparison_operator="GREATER_THAN",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS", address=budget_topic.topic_arn
                        ),
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=notification_email
                        ),
                    ],
                ),
            ],
        )


def _budgets_publish_statement(topic_arn: str):
    from aws_cdk import aws_iam as iam

    return iam.PolicyStatement(
        sid="AllowBudgetsPublish",
        principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
        actions=["SNS:Publish"],
        resources=[topic_arn],
    )
