import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import UNITS_PER_SUBMISSION, estimate_remaining, judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def _json_error(exc_cls, detail: str):
    return exc_cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise _json_error(web.HTTPUnauthorized, "未登录")
    return user


def require_writer(request: web.Request, detail: str = "仅记录员可提交读数") -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(web.HTTPForbidden, detail)
    return user


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT r.id, r.probe_id, r.temp_c, r.verdict, r.reason, r.status,
               r.created_by, r.created_at, r.processed_at, c.batch_id
        FROM probe_readings r
        LEFT JOIN refrigerant_consumptions c ON c.reading_id = r.id
        ORDER BY r.id DESC
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "probe_id": r["probe_id"],
                "temp_c": r["temp_c"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "status": r["status"],
                "created_by": r["created_by"],
                "batch_id": r["batch_id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
            }
        )
    return web.json_response(out)


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise _json_error(web.HTTPBadRequest, "探头编号不能为空")
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise _json_error(web.HTTPBadRequest, "温度必须是数字") from exc
    batch_id = str(body.get("batch_id") or "").strip()

    pool: asyncpg.Pool = request.app["pool"]
    # 读数、消耗明细、批次余量在同一个事务里写入：
    # 余量由服务端按“起始余量 - 明细合计”重算，绝不只改余量数字。
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
                VALUES ($1, $2, 'pending', $3, now())
                RETURNING id, probe_id, temp_c, verdict, reason, status, created_by, created_at, processed_at
                """,
                probe_id,
                temp_c,
                user["username"],
            )
            consumption = None
            remaining = None
            if batch_id:
                batch = await conn.fetchrow(
                    "SELECT batch_id FROM refrigerant_batches WHERE batch_id = $1 FOR UPDATE",
                    batch_id,
                )
                if not batch:
                    # 未知批次：整笔回滚，不得误算余量
                    raise _json_error(web.HTTPBadRequest, "批次不存在，请先维护批次起始余量")
                consumption = await conn.fetchrow(
                    """
                    INSERT INTO refrigerant_consumptions (batch_id, reading_id, units, created_by)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, units, created_at
                    """,
                    batch_id,
                    row["id"],
                    UNITS_PER_SUBMISSION,
                    user["username"],
                )
                remaining = await conn.fetchval(
                    """
                    UPDATE refrigerant_batches
                    SET remaining = start_balance - COALESCE((
                            SELECT SUM(units) FROM refrigerant_consumptions
                            WHERE batch_id = $1), 0),
                        updated_at = now()
                    WHERE batch_id = $1
                    RETURNING remaining
                    """,
                    batch_id,
                )

    resp = {
        "id": row["id"],
        "probe_id": row["probe_id"],
        "temp_c": row["temp_c"],
        "verdict": row["verdict"],
        "reason": row["reason"],
        "status": row["status"],
        "created_by": row["created_by"],
        "batch_id": batch_id or None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "processed_at": None,
    }
    if consumption:
        resp["consumption"] = {
            "batch_id": batch_id,
            "units": consumption["units"],
            "remaining": remaining,
        }
        resp["message"] = (
            f"已入队，后台工人将认领并判定；批次 {batch_id} 消耗 "
            f"{consumption['units']} 单位，服务端重算后剩余 {remaining}"
        )
    else:
        resp["message"] = "已入队，后台工人将认领并判定"
    return web.json_response(resp, status=201)


def _parse_non_negative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def _batch_view(row, consumed_units: int, entry_count: int) -> dict:
    # 余量永远由服务端按“起始余量 - 消耗明细合计”重算后返回，与明细对账
    return {
        "batch_id": row["batch_id"],
        "start_balance": row["start_balance"],
        "consumed_units": consumed_units,
        "remaining": estimate_remaining(row["start_balance"], consumed_units),
        "entry_count": entry_count,
        "unit_per_reading": UNITS_PER_SUBMISSION,
        "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def list_batches(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT b.batch_id, b.start_balance, b.created_by, b.created_at, b.updated_at,
               COALESCE(SUM(c.units), 0)::int AS consumed_units,
               COUNT(c.id)::int AS entry_count
        FROM refrigerant_batches b
        LEFT JOIN refrigerant_consumptions c ON c.batch_id = b.batch_id
        GROUP BY b.batch_id
        ORDER BY b.batch_id
        """
    )
    return web.json_response(
        [_batch_view(r, r["consumed_units"], r["entry_count"]) for r in rows]
    )


async def get_batch(request: web.Request) -> web.Response:
    require_user(request)
    batch_id = request.match_info["batch_id"].strip()
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        batch = await conn.fetchrow(
            """
            SELECT batch_id, start_balance, created_by, created_at, updated_at
            FROM refrigerant_batches
            WHERE batch_id = $1
            """,
            batch_id,
        )
        if not batch:
            # 未知批次：明确 404，不得误算出余量
            raise _json_error(web.HTTPNotFound, "批次不存在，无法估算余量")
        entries = await conn.fetch(
            """
            SELECT id, reading_id, units, created_by, created_at
            FROM refrigerant_consumptions
            WHERE batch_id = $1
            ORDER BY id
            """,
            batch_id,
        )
    consumed = sum(e["units"] for e in entries)
    out = _batch_view(batch, consumed, len(entries))
    out["entries"] = [
        {
            "id": e["id"],
            "reading_id": e["reading_id"],
            "units": e["units"],
            "created_by": e["created_by"],
            "created_at": e["created_at"].isoformat() if e["created_at"] else None,
        }
        for e in entries
    ]
    return web.json_response(out)


async def upsert_batch(request: web.Request) -> web.Response:
    user = require_writer(request, "仅记录员可维护批次余量")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    batch_id = str(body.get("batch_id", "")).strip()
    if not batch_id:
        raise _json_error(web.HTTPBadRequest, "批次编号不能为空")
    raw_start = body.get("start_balance")
    if raw_start is None or (isinstance(raw_start, str) and not raw_start.strip()):
        # 空起始：拒绝写入，不得误算出余量
        raise _json_error(web.HTTPBadRequest, "起始余量不能为空")
    start_balance = _parse_non_negative_int(raw_start)
    if start_balance is None:
        raise _json_error(web.HTTPBadRequest, "起始余量必须是非负整数")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO refrigerant_batches (batch_id, start_balance, remaining, created_by)
                VALUES ($1, $2, $2, $3)
                ON CONFLICT (batch_id) DO UPDATE
                SET start_balance = EXCLUDED.start_balance,
                    remaining = EXCLUDED.start_balance - COALESCE((
                        SELECT SUM(units) FROM refrigerant_consumptions
                        WHERE batch_id = EXCLUDED.batch_id), 0),
                    updated_at = now()
                RETURNING batch_id, start_balance, created_by, created_at, updated_at
                """,
                batch_id,
                start_balance,
                user["username"],
            )
            consumed = await conn.fetchval(
                "SELECT COALESCE(SUM(units), 0) FROM refrigerant_consumptions WHERE batch_id = $1",
                batch_id,
            )
            entry_count = await conn.fetchval(
                "SELECT COUNT(*) FROM refrigerant_consumptions WHERE batch_id = $1",
                batch_id,
            )
    out = _batch_view(row, consumed, entry_count)
    out["message"] = "已保存，服务端已按消耗明细重算余量"
    return web.json_response(out)


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/batches", list_batches)
    app.router.add_post("/api/batches", upsert_batch)
    app.router.add_get("/api/batches/{batch_id}", get_batch)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
