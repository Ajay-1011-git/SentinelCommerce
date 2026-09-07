"""GovernanceStack - AWS Config + a custom SSM Automation remediation.

Module 4 (self-healing governance): AWS Config detects an unrestricted
inbound SSH rule on the throwaway demo security group and a custom SSM
Automation document revokes it automatically, then logs the fix to SNS.

NOTE on the configuration recorder: an account/region may hold only ONE.
`scripts/predeploy_check.sh` runs
`aws configservice describe-configuration-recorders` and halts the deploy
with a clear message if one already exists - CloudFormation cannot express
that pre-check itself. RUNBOOK.md wires this into the deploy step.
"""
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_config as config
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_ssm as ssm
from constructs import Construct

# Managed Config rule that flags security groups allowing unrestricted
# inbound SSH (0.0.0.0/0 or ::/0 on port 22). Verify against current AWS
# Config "List of Managed Rules" docs before deploy - AWS has renamed rule
# source identifiers before. As of this writing the identifier is
# INCOMING_SSH_DISABLED (console name: "restricted-ssh").
RESTRICTED_SSH_RULE_IDENTIFIER = "INCOMING_SSH_DISABLED"

LOW_STOCK_PARAM_NAME = "/sentinelcommerce/low-stock-threshold"


class GovernanceStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        demo_security_group: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SSM Parameter Store: small non-secret config -------------
        ssm.StringParameter(
            self,
            "LowStockThreshold",
            parameter_name=LOW_STOCK_PARAM_NAME,
            string_value="5",
            description="stream_processor publishes a low-stock alert when stock drops below this",
        )

        # --- Governance alerts topic --------------------------------
        self.governance_alerts_topic = sns.Topic(
            self,
            "GovernanceAlertsTopic",
            topic_name="sentinelcommerce-governance-alerts",
        )

        # --- AWS Config: recorder + delivery channel + S3 -----------
        config_bucket = s3.Bucket(
            self,
            "ConfigBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(7))],
            # Demo, not production: cdk destroy must actually empty and
            # delete this bucket.
            auto_delete_objects=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        config_role = iam.Role(
            self,
            "ConfigRecorderRole",
            assumed_by=iam.ServicePrincipal("config.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWS_ConfigRole"
                )
            ],
        )
        config_bucket.grant_read_write(config_role)

        recorder = config.CfnConfigurationRecorder(
            self,
            "ConfigRecorder",
            role_arn=config_role.role_arn,
            recording_group=config.CfnConfigurationRecorder.RecordingGroupProperty(
                all_supported=False,
                resource_types=["AWS::EC2::SecurityGroup"],
            ),
        )
        delivery_channel = config.CfnDeliveryChannel(
            self,
            "ConfigDeliveryChannel",
            s3_bucket_name=config_bucket.bucket_name,
        )
        delivery_channel.add_dependency(recorder)

        # --- Config rule scoped to the demo SG ---------------------
        ssh_rule = config.CfnConfigRule(
            self,
            "RestrictedSshRule",
            config_rule_name="sentinelcommerce-restricted-ssh",
            source=config.CfnConfigRule.SourceProperty(
                owner="AWS",
                source_identifier=RESTRICTED_SSH_RULE_IDENTIFIER,
            ),
            scope=config.CfnConfigRule.ScopeProperty(
                compliance_resource_id=demo_security_group.security_group_id,
                compliance_resource_types=["AWS::EC2::SecurityGroup"],
            ),
        )
        ssh_rule.add_dependency(recorder)

        # --- Custom SSM Automation remediation document -----------
        # Written from scratch so it is fully under our control for the demo
        # (rather than depending on an AWS-managed doc under a guessed name).
        automation_role = iam.Role(
            self,
            "RemediationAutomationRole",
            assumed_by=iam.ServicePrincipal("ssm.amazonaws.com"),
        )
        automation_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:RevokeSecurityGroupIngress",
                    "ec2:DescribeSecurityGroups",
                ],
                resources=["*"],
            )
        )
        self.governance_alerts_topic.grant_publish(automation_role)

        remediation_doc = ssm.CfnDocument(
            self,
            "RevokeSshRemediationDoc",
            name="SentinelCommerce-RevokeUnrestrictedSsh",
            document_type="Automation",
            document_format="JSON",
            content={
                "schemaVersion": "0.3",
                "description": "Revoke 0.0.0.0/0:22 from a security group and log the fix to SNS.",
                "assumeRole": "{{ AutomationAssumeRole }}",
                "parameters": {
                    "AutomationAssumeRole": {"type": "String"},
                    "SecurityGroupId": {"type": "String"},
                },
                "mainSteps": [
                    {
                        "name": "RevokeInboundSsh",
                        "action": "aws:executeAwsApi",
                        "inputs": {
                            "Service": "ec2",
                            "Api": "RevokeSecurityGroupIngress",
                            "GroupId": "{{ SecurityGroupId }}",
                            "IpPermissions": [
                                {
                                    "IpProtocol": "tcp",
                                    "FromPort": 22,
                                    "ToPort": 22,
                                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                                }
                            ],
                        },
                    },
                    {
                        "name": "PublishFix",
                        "action": "aws:executeAwsApi",
                        "inputs": {
                            "Service": "sns",
                            "Api": "Publish",
                            "TopicArn": self.governance_alerts_topic.topic_arn,
                            "Subject": "SentinelCommerce auto-remediation",
                            "Message": "Revoked 0.0.0.0/0:22 from {{ SecurityGroupId }}",
                        },
                    },
                ],
            },
        )

        config.CfnRemediationConfiguration(
            self,
            "RestrictedSshRemediation",
            config_rule_name=ssh_rule.config_rule_name,
            target_type="SSM_DOCUMENT",
            target_id=remediation_doc.name,
            automatic=True,
            maximum_automatic_attempts=3,
            retry_attempt_seconds=60,
            parameters={
                "AutomationAssumeRole": config.CfnRemediationConfiguration.RemediationParameterValueProperty(
                    static_value=config.CfnRemediationConfiguration.StaticValueProperty(
                        values=[automation_role.role_arn]
                    )
                ),
                "SecurityGroupId": config.CfnRemediationConfiguration.RemediationParameterValueProperty(
                    resource_value=config.CfnRemediationConfiguration.ResourceValueProperty(
                        value="RESOURCE_ID"
                    )
                ),
            },
        ).add_dependency(ssh_rule)
