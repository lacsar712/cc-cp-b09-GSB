import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 每次成功提交读数固定消耗一个单位冷媒。
CONSUMPTION_UNIT = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 冷媒批次：记录员维护批次起始余量，余量只能由服务端按明细重算。
CREATE TABLE IF NOT EXISTS refrigerant_batches (
    id serial PRIMARY KEY,
    batch_no text NOT NULL UNIQUE,
    start_amount integer NOT NULL CHECK (start_amount > 0),
    remaining integer NOT NULL CHECK (remaining >= 0),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- 冷媒消耗明细：与读数同一次事务写入，余量与明细据此对账。
CREATE TABLE IF NOT EXISTS refrigerant_consumptions (
    id serial PRIMARY KEY,
    batch_id integer NOT NULL REFERENCES refrigerant_batches(id),
    amount integer NOT NULL CHECK (amount > 0),
    reading_id integer REFERENCES probe_readings(id),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_consumptions_batch
    ON refrigerant_consumptions (batch_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consumption_reading
    ON refrigerant_consumptions (reading_id)
    WHERE reading_id IS NOT NULL;
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason),
        )
    conn.commit()
