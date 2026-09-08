"""create_order - writes an order to RDS MySQL via the primary endpoint.

Runs in the isolated subnets (talks only to RDS). The DB password is
injected into the environment at deploy time from the SSM parameter
`/sentinelcommerce/db-password` - no runtime SSM call, so no VPC endpoint
and no NAT gateway are needed.
"""
import json
import logging
import os
import uuid

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_HOST = os.environ["DB_ENDPOINT"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ.get("DB_NAME", "sentinelcommerce")


def _log(event_name, **fields):
    logger.info(json.dumps({"event": event_name, **fields}))


def _connect():
    import pymysql

    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connect_timeout=5,
    )


def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    order_id = str(uuid.uuid4())
    sku = body.get("sku", "UNKNOWN")
    qty = int(body.get("qty", 1))
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS orders "
                "(order_id VARCHAR(64) PRIMARY KEY, sku VARCHAR(64), qty INT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            cur.execute(
                "INSERT INTO orders (order_id, sku, qty) VALUES (%s, %s, %s)",
                (order_id, sku, qty),
            )
        conn.commit()
        conn.close()
        _log("order_created", order_id=order_id, sku=sku, qty=qty)
        return {"statusCode": 201, "body": json.dumps({"order_id": order_id})}
    except Exception as exc:  # noqa: BLE001
        _log("order_failed", error=str(exc))
        return {"statusCode": 500, "body": json.dumps({"error": "order failed"})}
