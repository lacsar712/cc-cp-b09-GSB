import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import CONSUMPTION_UNIT, create_pool, ensure_schema_async, seed_if_empty

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _json_error(status: int, detail: str) -> web.HTTPException:
    cls = {
        400: web.HTTPBadRequest,
        401: web.HTTPUnauthorized,
        403: web.HTTPForbidden,
        404: web.HTTPNotFound,
        409: web.HTTPConflict,
    }[status]
    return cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


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


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise _json_error(401, "未登录")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(403, "仅记录员可执行该操作")
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
        raise _json_error(401, "用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


def _parse_start_amount(raw) -> int:
    # 空起始或非整数一律拒绝，绝不能误算出余量。
    if raw is None or isinstance(raw, bool):
        raise _json_error(400, "批次起始余量必须为正整数")
    if isinstance(raw, int):
        value = raw
    else:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise _json_error(400, "批次起始余量必须为正整数") from exc
    if value <= 0:
        raise _json_error(400, "批次起始余量必须为正整数")
    return value


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, verdict, reason, status, created_by,
               created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
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
        raise _json_error(400, "探头编号不能为空")
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise _json_error(400, "温度必须是数字") from exc
    # 未知批次不得提交，也不得误算出余量。
    batch_no = str(body.get("batch_no", "")).strip()
    if not batch_no:
        raise _json_error(400, "冷媒批次不能为空")

    pool: asyncpg.Pool = request.app["pool"]
    # 读数、消耗明细、余量重算必须在同一事务内完成：
    # 只写明细不重算余量、或只改余量不写明细都不允许。
    async with pool.acquire() as conn:
        async with conn.transaction():
            batch = await conn.fetchrow(
                """
                SELECT id, start_amount, remaining
                FROM refrigerant_batches
                WHERE batch_no = $1
                FOR UPDATE
                """,
                batch_no,
            )
            if not batch:
                raise _json_error(400, f"未知批次：{batch_no}")
            if batch["remaining"] < CONSUMPTION_UNIT:
                raise _json_error(400, f"批次 {batch_no} 冷媒余量不足")

            reading = await conn.fetchrow(
                """
                INSERT INTO probe_readings (probe_id, temp_c, status, created_by, created_at)
                VALUES ($1, $2, 'pending', $3, now())
                RETURNING id, probe_id, temp_c, verdict, reason, status,
                          created_by, created_at, processed_at
                """,
                probe_id,
                temp_c,
                user["username"],
            )
            await conn.execute(
                """
                INSERT INTO refrigerant_consumptions
                    (batch_id, amount, reading_id, created_by, created_at)
                VALUES ($1, $2, $3, $4, now())
                """,
                batch["id"],
                CONSUMPTION_UNIT,
                reading["id"],
                user["username"],
            )
            # 余量由服务端依据明细重算，前端不得私下相减。
            new_remaining = await conn.fetchval(
                """
                UPDATE refrigerant_batches b
                SET remaining = b.start_amount -
                    COALESCE((
                        SELECT SUM(c.amount)
                        FROM refrigerant_consumptions c
                        WHERE c.batch_id = b.id
                    ), 0)
                WHERE b.id = $1
                RETURNING remaining
                """,
                batch["id"],
            )

    return web.json_response(
        {
            "id": reading["id"],
            "probe_id": reading["probe_id"],
            "temp_c": reading["temp_c"],
            "verdict": reading["verdict"],
            "reason": reading["reason"],
            "status": reading["status"],
            "created_by": reading["created_by"],
            "created_at": reading["created_at"].isoformat() if reading["created_at"] else None,
            "processed_at": None,
            "batch_no": batch_no,
            "remaining": new_remaining,
            "message": "已入队，后台工人将认领并判定；冷媒余量已由服务端重算",
        },
        status=201,
    )


async def create_batch(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    batch_no = str(body.get("batch_no", "")).strip()
    if not batch_no:
        raise _json_error(400, "批次号不能为空")
    start_amount = _parse_start_amount(body.get("start_amount"))

    pool: asyncpg.Pool = request.app["pool"]
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO refrigerant_batches
                (batch_no, start_amount, remaining, created_by, created_at)
            VALUES ($1, $2, $2, $3, now())
            RETURNING id, batch_no, start_amount, remaining, created_by, created_at
            """,
            batch_no,
            start_amount,
            user["username"],
        )
    except asyncpg.UniqueViolationError as exc:
        raise _json_error(409, f"批次 {batch_no} 已存在") from exc
    return web.json_response(
        {
            "id": row["id"],
            "batch_no": row["batch_no"],
            "start_amount": row["start_amount"],
            "remaining": row["remaining"],
            "consumed": 0,
            "created_by": row["created_by"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "message": "批次已登记，余量以服务端重算为准",
        },
        status=201,
    )


async def list_batches(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT b.id, b.batch_no, b.start_amount, b.remaining,
               b.created_by, b.created_at,
               COALESCE(s.consumed, 0) AS consumed
        FROM refrigerant_batches b
        LEFT JOIN (
            SELECT batch_id, SUM(amount) AS consumed
            FROM refrigerant_consumptions
            GROUP BY batch_id
        ) s ON s.batch_id = b.id
        ORDER BY b.id DESC
        """
    )
    out = []
    for r in rows:
        # 对账：明细合计必须等于 起始余量 - 当前余量。
        consumed = int(r["consumed"])
        remaining = int(r["remaining"])
        start = int(r["start_amount"])
        out.append(
            {
                "id": r["id"],
                "batch_no": r["batch_no"],
                "start_amount": start,
                "remaining": remaining,
                "consumed": consumed,
                "balanced": start - consumed == remaining,
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
        )
    return web.json_response(out)


async def list_consumptions(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    batch_no = request.query.get("batch_no", "").strip()
    if batch_no:
        rows = await pool.fetch(
            """
            SELECT c.id, c.batch_id, b.batch_no, c.amount, c.reading_id,
                   c.created_by, c.created_at
            FROM refrigerant_consumptions c
            JOIN refrigerant_batches b ON b.id = c.batch_id
            WHERE b.batch_no = $1
            ORDER BY c.id DESC
            """,
            batch_no,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT c.id, c.batch_id, b.batch_no, c.amount, c.reading_id,
                   c.created_by, c.created_at
            FROM refrigerant_consumptions c
            JOIN refrigerant_batches b ON b.id = c.batch_id
            ORDER BY c.id DESC
            """
        )
    return web.json_response(
        [
            {
                "id": r["id"],
                "batch_id": r["batch_id"],
                "batch_no": r["batch_no"],
                "amount": r["amount"],
                "reading_id": r["reading_id"],
                "created_by": r["created_by"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    )


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
    app.router.add_post("/api/batches", create_batch)
    app.router.add_get("/api/consumptions", list_consumptions)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
