"""get_reports - reads order aggregates from Aurora via the READER endpoint.

Reports deliberately hit the reader endpoint, NOT the writer: analytical
/ reporting queries are isolated from live checkout traffic so a slow
report can never contend with order writes on the writer instance.
"""
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

READER_ENDPOINT = os.environ["DB_READER_ENDPOINT"]
SECRET_ARN = os.environ["DB_SECRET_ARN"]
DB_NAME = os.environ.get("DB_NAME", "sentinelcommerce")


def handler(event, context):
    import boto3
    import pymysql

    try:
        secret = json.loads(
            boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ARN)[
                "SecretString"
            ]
        )
        conn = pymysql.connect(
            host=READER_ENDPOINT,
            user=secret["username"],
            password=secret["password"],
            database=DB_NAME,
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sku, COUNT(*) orders, COALESCE(SUM(qty),0) units "
                "FROM orders GROUP BY sku"
            )
            rows = [
                {"sku": r[0], "orders": int(r[1]), "units": int(r[2])}
                for r in cur.fetchall()
            ]
        conn.close()
        logger.info(json.dumps({"event": "reports_served", "groups": len(rows)}))
        return {"statusCode": 200, "body": json.dumps({"reports": rows})}
    except Exception as exc:  # noqa: BLE001
        logger.info(json.dumps({"event": "reports_failed", "error": str(exc)}))
        return {"statusCode": 500, "body": json.dumps({"error": "report failed"})}
