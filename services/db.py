# services/db.py
import os
import psycopg2
import psycopg2.extras

def get_conn():
    # Configure via env vars:
    # PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "contagem_sacaria"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "Coop@2025")
    )
    return conn

def query_all(sql, params=None):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None

def execute(sql, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        conn.commit()

def execute_returning(sql, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
