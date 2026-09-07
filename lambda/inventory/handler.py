"""get_inventory / update_cart - DynamoDB reads and writes.

One asset directory, two Lambda functions in ComputeStack pointing at the
two handlers below. Item model on the single table:

  Inventory item : PK = "PRODUCT#<sku>"          SK = "INVENTORY"
                   attrs: stock (N), name (S)
  Cart line      : PK = "CART#<cart_id>"         SK = "ITEM#<sku>"
                   attrs: qty (N)
"""
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ["TABLE_NAME"]
_table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _resp(code, payload):
    return {"statusCode": code, "body": json.dumps(payload, default=str)}


def get_inventory(event, context):
    """GET /inventory or /inventory/{sku}"""
    sku = (event.get("pathParameters") or {}).get("sku")
    if sku:
        item = _table.get_item(
            Key={"PK": f"PRODUCT#{sku}", "SK": "INVENTORY"}
        ).get("Item")
        return _resp(200 if item else 404, item or {"error": "not found"})
    resp = _table.scan(
        FilterExpression="SK = :sk",
        ExpressionAttributeValues={":sk": "INVENTORY"},
    )
    logger.info(json.dumps({"event": "inventory_listed", "count": resp["Count"]}))
    return _resp(200, {"items": resp["Items"]})


def update_cart(event, context):
    """POST /cart  body: {cart_id, sku, qty}"""
    body = json.loads(event.get("body") or "{}")
    cart_id, sku = body["cart_id"], body["sku"]
    qty = int(body.get("qty", 1))
    _table.put_item(
        Item={"PK": f"CART#{cart_id}", "SK": f"ITEM#{sku}", "qty": qty}
    )
    # Decrement stock; a DynamoDB Stream record fires stream_processor, which
    # decides whether the low-stock SNS alert should publish.
    _table.update_item(
        Key={"PK": f"PRODUCT#{sku}", "SK": "INVENTORY"},
        UpdateExpression="SET stock = if_not_exists(stock, :z) - :q",
        ExpressionAttributeValues={":q": qty, ":z": 0},
    )
    logger.info(
        json.dumps({"event": "cart_updated", "cart_id": cart_id, "sku": sku, "qty": qty})
    )
    return _resp(200, {"cart_id": cart_id, "sku": sku, "qty": qty})
