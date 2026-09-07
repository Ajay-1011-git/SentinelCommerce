"""NetworkStack - the VPC every other stack builds on.

Module 1 talking points:
  * Three-tier subnetting so the database has *no* route to the internet.
  * A single NAT gateway - a deliberate cost tradeoff for a demo.
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
            # COST TRADEOFF: exactly one NAT gateway, not one-per-AZ. NAT
            # gateways bill per hour (~$0.045/hr in ap-south-1) plus data
            # processing regardless of traffic. One NAT means a single-AZ
            # failure could cut Lambda egress, which is an acceptable risk
            # for a time-boxed demo but would not be for production.
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateWithEgress",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    # PRIVATE_ISOLATED: no NAT, no IGW route. Aurora lives
                    # here so the database is unreachable from the internet
                    # by construction, not just by security group.
                    name="PrivateIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )
