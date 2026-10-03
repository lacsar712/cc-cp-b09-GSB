# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 冷媒余量估算

顶栏「余量估算」落地页包含批次余量输入、估算结果与消耗明细：

- 记录员在「批次余量输入」维护批次起始余量（`POST /api/batches`，值班员 403 只读）。
- 提交读数时填批次编号，每次成功提交固定消耗 **1 单位**：读数、消耗明细、批次余量在**同一个事务**写入，余量由服务端按 `起始余量 − 消耗明细合计` 重算，前端只展示服务端结果，不私下相减。
- 空起始余量或未知批次一律报错（400/404），不会误算出余量；未知批次提交读数会整笔回滚。
- `GET /api/batches` 列出全部批次（含剩余余量与明细条数），`GET /api/batches/{批次}` 返回估算结果与消耗明细，二者始终对账。

例：批次起始 10，成功提交两笔读数后，余量 = 8，消耗明细 = 2 条。

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
