"""stream_processor - DynamoDB Streams consumer for the real-time pipeline.

On every INVENTORY item change where stock crosses BELOW the low-stock
threshold (old image at/above, new image below), publish to the
inventory-alerts SNS topic. The threshold is read from SSM Parameter
Store - never hardcoded - so the professor can change it live and see the
pipeline behaviour shift without a redeploy.
"""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TOPIC_ARN = os.environ["ALERT_TOPIC_ARN"]
THRESHOLD_PARAM = os.environ["LOW_STOCK_PARAM_NAME"]

_sns = boto3.client("sns")
_ssm = boto3.client("ssm")


def _threshold():
    return int(_ssm.get_parameter(Name=THRESHOLD_PARAM)["Parameter"]["Value"])


def _num(image, key):
    if image and key in image and "N" in image[key]:
        return int(image[key]["N"])
    return None


def handler(event, context):
    threshold = _threshold()
    for record in event.get("Records", []):
        if record.get("eventName") not in ("INSERT", "MODIFY"):
            continue
        keys = record["dynamodb"].get("Keys", {})
        if keys.get("SK", {}).get("S") != "INVENTORY":
            continue
        new_img = record["dynamodb"].get("NewImage", {})
        old_img = record["dynamodb"].get("OldImage", {})
        new_stock = _num(new_img, "stock")
        old_stock = _num(old_img, "stock")
        if new_stock is None:
            continue
        crossed = new_stock < threshold and (old_stock is None or old_stock >= threshold)
        if not crossed:
            continue
        sku = keys.get("PK", {}).get("S", "").replace("PRODUCT#", "")
        msg = {
            "alert": "LOW_STOCK",
            "sku": sku,
            "stock": new_stock,
            "threshold": threshold,
        }
        _sns.publish(
            TopicArn=TOPIC_ARN,
            Subject=f"SentinelCommerce low stock: {sku}",
            Message=json.dumps(msg),
        )
        logger.info(json.dumps({"event": "low_stock_published", **msg}))
    return {"processed": len(event.get("Records", []))}
