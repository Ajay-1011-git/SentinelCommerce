"""get_reports - order aggregates from RDS MySQL.

In the $0 design there is only one RDS instance; when a read replica is
promoted for the Act 1 resilience demo it becomes a standalone primary and
this function is repointed at it (env var updated by the RUNBOOK step).
Reports still run on a connection separate from checkout writes.
"""
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_HOST = os.environ["DB_READER_ENDPOINT"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = os.environ.get("DB_NAME", "sentinelcommerce")


def handler(event, context):
    import pymysql

    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sku, COUNT(*) AS orders, COALESCE(SUM(qty),0) AS units "
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
