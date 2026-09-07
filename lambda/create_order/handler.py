"""create_order - writes an order to Aurora via the WRITER endpoint.

Structured JSON logging only. DB credentials are pulled from Secrets
Manager at runtime - nothing sensitive is in the environment.
"""
import json
import logging
import os
import uuid

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WRITER_ENDPOINT = os.environ["DB_WRITER_ENDPOINT"]
SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_NAME = os.environ.get("DB_NAME", "sentinelcommerce")


def _log(event_name, **fields):
    logger.info(json.dumps({"event": event_name, **fields}))


def _connect():
    import boto3
    import pymysql

    secret = json.loads(
        boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ARN)[
            "SecretString"
        ]
    )
    return pymysql.connect(
        host=WRITER_ENDPOINT,
        user=secret["username"],
        password=secret["password"],
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
    except Exception as exc:  # noqa: BLE001 - surface failure to caller + logs
        _log("order_failed", error=str(exc))
        return {"statusCode": 500, "body": json.dumps({"error": "order failed"})}
