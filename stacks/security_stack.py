"""SecurityStack - KMS, WAFv2, and the throwaway remediation-target SG.

Module 3 (blocked web attack) and Module 4 (self-healing governance)
both start here.
"""
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct


class SecurityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- KMS: one key, three layers -------------------------------------
        # ONE customer-managed key encrypts Aurora storage, the DynamoDB
        # table, and the Secrets Manager DB secret. "One key, three layers"
        # is a deliberate defense-in-depth talking point: a single revoke or
        # disable of this key instantly cuts access to every data surface.
        # The key policy is scoped - decrypt is granted only to the specific
        # Lambda execution roles that need it (added later via
        # grant_decrypt / grant_encrypt_decrypt from ComputeStack) plus the
        # account root for admin/break-glass. It is never "*".
        # The default CDK key policy already grants the account root full
        # admin on the key (break-glass) AND enables IAM-identity-based
        # grants, so `kms_key.grant_decrypt(lambda_role)` calls from
        # ComputeStack land only on each Lambda role's own policy - the key
        # policy stays minimal and there is no Security<->Compute cycle.
        # We do NOT widen this to "*".
        self.kms_key = kms.Key(
            self,
            "SentinelKey",
            alias="alias/sentinelcommerce",
            description="SentinelCommerce - encrypts Aurora, DynamoDB and the DB secret",
            enable_key_rotation=True,
        )

        # --- WAFv2 REGIONAL WebACL ----------------------------------------
        # Created here; ComputeStack owns the association to the REST API
        # stage (avoids a circular stack dependency). REST API + REGIONAL
        # scope is the most reliable WAF association path per current AWS
        # docs (CfnWebACLAssociation with the stage ARN).
        common_visibility = wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True,
            sampled_requests_enabled=True,
            metric_name="sentinelcommerce-webacl",
        )

        rules = [
            wafv2.CfnWebACL.RuleProperty(
                name="AWS-AWSManagedRulesCommonRuleSet",
                priority=1,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name="AWSManagedRulesCommonRuleSet",
                    )
                ),
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    sampled_requests_enabled=True,
                    metric_name="common-rule-set",
                ),
            ),
            wafv2.CfnWebACL.RuleProperty(
                name="AWS-AWSManagedRulesSQLiRuleSet",
                priority=2,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name="AWSManagedRulesSQLiRuleSet",
                    )
                ),
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    sampled_requests_enabled=True,
                    metric_name="sqli-rule-set",
                ),
            ),
            wafv2.CfnWebACL.RuleProperty(
                name="RateLimitPerIP",
                priority=3,
                # Custom rate-based rule: block any single IP doing more than
                # 100 requests in a rolling 5-minute window. 100 is the
                # minimum WAF allows and makes the demo easy to trigger with
                # a short loop of curl calls.
                statement=wafv2.CfnWebACL.StatementProperty(
                    rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=100,
                        evaluation_window_sec=300,
                        aggregate_key_type="IP",
                    )
                ),
                action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    sampled_requests_enabled=True,
                    metric_name="rate-limit-per-ip",
                ),
            ),
        ]

        self.web_acl = wafv2.CfnWebACL(
            self,
            "SentinelWebAcl",
            name="sentinelcommerce-web-acl",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=common_visibility,
            rules=rules,
        )
        self.web_acl_arn = self.web_acl.attr_arn
        self.web_acl_name = self.web_acl.name

        # --- demo-remediation-target security group -----------------------
        # Intentionally attached to NOTHING. It exists only so the
        # governance demo can open port 22 to 0.0.0.0/0 on a group that
        # protects no real resource - the SG guarding Aurora is never
        # touched. AWS Config flags this, and our custom SSM Automation
        # document revokes the rule automatically.
        self.demo_remediation_sg = ec2.SecurityGroup(
            self,
            "DemoRemediationTargetSg",
            vpc=vpc,
            description="demo-remediation-target - unused; governance demo blast target only",
            allow_all_outbound=False,
        )
