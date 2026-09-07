#!/usr/bin/env python3
"""seed_data.py - put a few demo products and orders into the deployed stack.

Reads resource names from CloudFormation outputs / conventional names, so
run it AFTER `cdk deploy --all`. Requires boto3 and AWS credentials for the
target account (ap-south-1 by default).

  python scripts/seed_data.py
"""
import json
import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "ap-south-1")

PRODUCTS = [
    {"sku": "SC-KEYB-01", "name": "Mechanical Keyboard", "stock": 12},
    {"sku": "SC-MOUSE-02", "name": "Wireless Mouse", "stock": 30},
    {"sku": "SC-HUB-03", "name": "USB-C Hub", "stock": 6},
    {"sku": "SC-CABLE-04", "name": "Braided Cable", "stock": 4},  # already low
]


def _dynamo_table_name(cfn):
    out = cfn.describe_stacks(StackName="SentinelCommerce-DataStack")["Stacks"][0]
    for o in out.get("Outputs", []):
        if "Table" in o["OutputKey"]:
            return o["OutputValue"]
    # fall back to a scan of tables tagged for the project
    ddb = boto3.client("dynamodb", region_name=REGION)
    for name in ddb.list_tables()["TableNames"]:
        tags = ddb.list_tags_of_resource(
            ResourceArn=ddb.describe_table(TableName=name)["Table"]["TableArn"]
        )["Tags"]
        if any(t["Key"] == "Project" and t["Value"] == "SentinelCommerce" for t in tags):
            return name
    raise SystemExit("Could not locate the SentinelCommerce DynamoDB table")


def main():
    cfn = boto3.client("cloudformation", region_name=REGION)
    table_name = _dynamo_table_name(cfn)
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
    print(f"Seeding DynamoDB table: {table_name}")

    with table.batch_writer() as batch:
        for p in PRODUCTS:
            batch.put_item(
                Item={
                    "PK": f"PRODUCT#{p['sku']}",
                    "SK": "INVENTORY",
                    "name": p["name"],
                    "stock": p["stock"],
                }
            )
    print(f"  wrote {len(PRODUCTS)} inventory items")

    # A couple of demo orders straight into Aurora would need VPC access;
    # instead, hit the deployed API if its URL is available.
    api_url = os.environ.get("SENTINEL_API_URL")
    if api_url:
        import urllib.request

        for sku, qty in (("SC-KEYB-01", 1), ("SC-MOUSE-02", 2)):
            req = urllib.request.Request(
                f"{api_url.rstrip('/')}/orders",
                data=json.dumps({"sku": sku, "qty": qty}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                print(f"  order {sku} x{qty}: {resp.status} {resp.read().decode()}")
    else:
        print("  set SENTINEL_API_URL to also seed demo orders through the API")


if __name__ == "__main__":
    sys.exit(main())
