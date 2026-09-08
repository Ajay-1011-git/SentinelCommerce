"""NetworkStack - a genuinely $0 VPC.

Zero-cost refactor:
  * NO NAT gateway (`nat_gateways=0`) - NAT gateways bill ~$0.045/hr + data
    regardless of use, and have no free tier.
  * NO `PRIVATE_WITH_EGRESS` subnet group - that tier only makes sense with
    a NAT gateway. Lambdas that need AWS APIs (DynamoDB, SNS, SSM) run with
    default Lambda networking (outside the VPC) and reach those services
    over the public AWS API at no cost.
  * Only `PRIVATE_ISOLATED` subnets across 2 AZs - for the RDS instance and
    the two Lambdas that talk to it. Isolated subnets have no route out, so
    there is nothing billable here: no IGW data, no NAT, no endpoints.

Subnets, route tables, security groups and the VPC itself are all free.
"""
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "SentinelVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=0,  # $0: no NAT gateway anywhere
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    # The only subnet tier. RDS + the two RDS-facing Lambdas
                    # live here. No route to the internet by construction.
                    name="PrivateIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
