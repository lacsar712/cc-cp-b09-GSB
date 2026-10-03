# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

冷媒余量按批次估算：顶栏「余量估算」落地页可登记批次起始余量、查看估算结果与消耗明细。每次成功提交读数固定消耗 1 个单位，**余量由服务端在同一事务内按明细重算**，前端只展示服务端数字，不做本地相减；值班侧只读，不能改余量。

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

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/batches` | 记录员 | 登记批次与起始余量（正整数，空起始/0/负数/非整数拒绝，重复批次 409） |
| GET | `/api/batches` | 登录用户 | 批次估算结果：`start_amount`/`consumed`/`remaining`/`balanced` 对账标记 |
| GET | `/api/consumptions` | 登录用户 | 消耗明细，可按 `?batch_no=` 过滤 |
| POST | `/api/readings` | 记录员 | 须带 `batch_no`；未知批次/空批次拒绝 |

规则：

- 每次成功提交读数固定消耗 `CONSUMPTION_UNIT`（=1）个单位。
- 读数、消耗明细、余量重算在**同一事务**完成（批次行 `FOR UPDATE` 加锁），余量与明细要么一起写、要么都不写；并发不会超扣。
- 余量公式 `remaining = start_amount - Σ消耗明细`，始终由服务端重算；前端禁止私下相减。
- 起始余量为空或批次未知时不会产生读数、明细，也不会误算出余量。
- 值班员（reader）可查看全部页面数字与明细，登记批次、提交读数均返回 403。


## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
