import { useCallback, useEffect, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function formatTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return iso;
  }
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("readings");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "", batch_no: "" });
  const [batchForm, setBatchForm] = useState({ batch_no: "", start_amount: "" });
  const [rows, setRows] = useState([]);
  const [batches, setBatches] = useState([]);
  const [consumptions, setConsumptions] = useState([]);
  const [error, setError] = useState("");
  const [batchError, setBatchError] = useState("");
  const [msg, setMsg] = useState("");
  const [batchMsg, setBatchMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  // 余量、消耗明细一律以服务端返回为准，前端只拉取展示，绝不本地相减。
  const loadBatches = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/batches", { headers: authHeaders() });
    if (res.ok) setBatches(await res.json());
  }, [token, authHeaders]);

  const loadConsumptions = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/consumptions", { headers: authHeaders() });
    if (res.ok) setConsumptions(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    if (!token) return undefined;
    loadReadings();
    loadBatches();
    loadConsumptions();
    const t = setInterval(() => {
      loadReadings();
      loadBatches();
      loadConsumptions();
    }, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadBatches, loadConsumptions, token]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setBatches([]);
    setConsumptions([]);
    setView("readings");
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
          batch_no: submitForm.batch_no,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        return;
      }
      // 只展示服务端重算后的数字，不做任何前端本地扣减。
      setMsg(`${data.message}（批次 ${data.batch_no} 当前余量 ${data.remaining}）`);
      setSubmitForm({ probe_id: "", temp_c: "", batch_no: "" });
      await Promise.all([loadReadings(), loadBatches(), loadConsumptions()]);
    } finally {
      setLoading(false);
    }
  }

  async function onCreateBatch(e) {
    e.preventDefault();
    setBatchError("");
    setBatchMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/batches", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          batch_no: batchForm.batch_no,
          start_amount: batchForm.start_amount === "" ? null : Number(batchForm.start_amount),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setBatchError(data.detail || "批次登记失败");
        return;
      }
      setBatchMsg(data.message || "批次已登记");
      setBatchForm({ batch_no: "", start_amount: "" });
      await Promise.all([loadBatches(), loadConsumptions()]);
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  function EstimatePage() {
    return (
      <>
        {isWriter ? (
          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>登记批次起始余量</h2>
            <p class="sub" style={{ marginBottom: "0.75rem", marginTop: 0 }}>
              起始余量由记录员维护；每次成功提交读数固定消耗 1 个单位，余量由服务端按明细重算。
            </p>
            <form onSubmit={onCreateBatch}>
              <div class="row">
                <label>
                  批次号
                  <input
                    required
                    value={batchForm.batch_no}
                    onInput={(e) =>
                      setBatchForm({ ...batchForm, batch_no: e.target.value })
                    }
                    placeholder="例如 R2026-10"
                  />
                </label>
                <label>
                  起始余量（正整数）
                  <input
                    required
                    type="number"
                    min="1"
                    step="1"
                    value={batchForm.start_amount}
                    onInput={(e) =>
                      setBatchForm({ ...batchForm, start_amount: e.target.value })
                    }
                  />
                </label>
                <button type="submit" disabled={loading}>
                  登记批次
                </button>
              </div>
              {batchError && <p class="err">{batchError}</p>}
              {batchMsg && <p class="ok">{batchMsg}</p>}
            </form>
          </div>
        ) : (
          <div class="card">
            <p class="sub" style={{ margin: 0 }}>
              值班侧只读：可查看批次余量与消耗明细，不能登记批次或修改余量。
            </p>
          </div>
        )}

        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>估算结果</h2>
          <table>
            <thead>
              <tr>
                <th>批次</th>
                <th>起始余量</th>
                <th>已消耗</th>
                <th>剩余余量</th>
                <th>对账</th>
                <th>登记人</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id}>
                  <td>{b.batch_no}</td>
                  <td>{b.start_amount}</td>
                  <td>{b.consumed}</td>
                  <td><strong>{b.remaining}</strong></td>
                  <td>
                    <span class={b.balanced ? "tag pass" : "tag fail"}>
                      {b.balanced ? "一致" : "不平"}
                    </span>
                  </td>
                  <td>{b.created_by}</td>
                </tr>
              ))}
              {batches.length === 0 && (
                <tr>
                  <td colspan="6">暂无批次</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>消耗明细</h2>
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>批次</th>
                <th>消耗量</th>
                <th>关联读数</th>
                <th>提交人</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {consumptions.map((c) => (
                <tr key={c.id}>
                  <td>{c.id}</td>
                  <td>{c.batch_no}</td>
                  <td>{c.amount}</td>
                  <td>{c.reading_id ?? "—"}</td>
                  <td>{c.created_by}</td>
                  <td>{formatTime(c.created_at)}</td>
                </tr>
              ))}
              {consumptions.length === 0 && (
                <tr>
                  <td colspan="6">暂无消耗明细</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button
            type="button"
            class={view === "readings" ? "secondary" : "secondary outline"}
            style={{ marginLeft: "0.5rem" }}
            onClick={() => setView("readings")}
          >
            读数台
          </button>
          <button
            type="button"
            class={view === "estimate" ? "secondary" : "secondary outline"}
            style={{ marginLeft: "0.5rem" }}
            onClick={() => setView("estimate")}
          >
            余量估算
          </button>
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "estimate" ? (
        <EstimatePage />
      ) : (
        <>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
                      placeholder="例如 探头C03"
                    />
                  </label>
                  <label>
                    温度（℃）
                    <input
                      required
                      type="number"
                      step="0.1"
                      value={submitForm.temp_c}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    冷媒批次
                    {batches.length > 0 ? (
                      <select
                        required
                        value={submitForm.batch_no}
                        onChange={(e) =>
                          setSubmitForm({ ...submitForm, batch_no: e.target.value })
                        }
                      >
                        <option value="">请选择批次</option>
                        {batches.map((b) => (
                          <option key={b.id} value={b.batch_no}>
                            {b.batch_no}（余量 {b.remaining}）
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        disabled
                        value="请先在余量估算页登记批次"
                        readonly
                      />
                    )}
                  </label>
                  <button type="submit" disabled={loading || batches.length === 0}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>探头</th>
                  <th>温度℃</th>
                  <th>结论</th>
                  <th>说明</th>
                  <th>状态</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>{r.probe_id}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{r.status}</td>
                    <td>{r.created_by}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan="7">暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
