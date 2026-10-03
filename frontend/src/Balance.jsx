import { useCallback, useEffect, useState } from "preact/hooks";

// 余量估算落地页：批次余量输入（记录员）+ 估算结果 + 消耗明细 + 批次总览。
// 页面上的余量数字一律来自服务端响应，前端不做任何“起始 - 消耗”的私下相减。
export function BalancePage({ user, authHeaders }) {
  const isWriter = user?.role === "writer";
  const [batches, setBatches] = useState([]);
  const [form, setForm] = useState({ batch_id: "", start_balance: "" });
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [queryId, setQueryId] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [estimate, setEstimate] = useState(null);
  const [estError, setEstError] = useState("");
  const [loading, setLoading] = useState(false);

  const loadBatches = useCallback(async () => {
    const res = await fetch("/api/batches", { headers: authHeaders() });
    if (res.ok) setBatches(await res.json());
  }, [authHeaders]);

  const loadEstimate = useCallback(
    async (batchId) => {
      const id = (batchId || "").trim();
      if (!id) {
        setEstimate(null);
        setEstError("请输入批次编号再查询");
        return;
      }
      const res = await fetch(`/api/batches/${encodeURIComponent(id)}`, {
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // 未知批次：只提示错误，不展示任何余量数字
        setEstimate(null);
        setEstError(data.detail || "批次不存在，无法估算余量");
        return;
      }
      setEstError("");
      setEstimate(data);
    },
    [authHeaders]
  );

  useEffect(() => {
    loadBatches();
    const t = setInterval(loadBatches, 3000);
    return () => clearInterval(t);
  }, [loadBatches]);

  useEffect(() => {
    if (!selectedId) return undefined;
    loadEstimate(selectedId);
    const t = setInterval(() => loadEstimate(selectedId), 3000);
    return () => clearInterval(t);
  }, [selectedId, loadEstimate]);

  async function onSaveStart(e) {
    e.preventDefault();
    setMsg("");
    setError("");
    const batch_id = form.batch_id.trim();
    if (!batch_id) {
      setError("批次编号不能为空");
      return;
    }
    if (String(form.start_balance).trim() === "") {
      setError("起始余量不能为空");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/batches", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ batch_id, start_balance: form.start_balance }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "保存失败");
        return;
      }
      setMsg(`已保存：批次 ${data.batch_id} 起始 ${data.start_balance}，服务端重算后剩余 ${data.remaining}`);
      setForm({ batch_id: "", start_balance: "" });
      await loadBatches();
      if (selectedId === data.batch_id) await loadEstimate(data.batch_id);
    } finally {
      setLoading(false);
    }
  }

  function onQuery(e) {
    e.preventDefault();
    const id = queryId.trim();
    if (!id) {
      setSelectedId("");
      setEstimate(null);
      setEstError("请输入批次编号再查询");
      return;
    }
    setSelectedId(id);
    loadEstimate(id);
  }

  function onPick(batchId) {
    setQueryId(batchId);
    setSelectedId(batchId);
    loadEstimate(batchId);
  }

  return (
    <div>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>批次余量输入</h2>
          <form onSubmit={onSaveStart}>
            <div class="row">
              <label>
                批次编号
                <input
                  required
                  value={form.batch_id}
                  onInput={(e) => setForm({ ...form, batch_id: e.target.value })}
                  placeholder="例如 冷媒批A01"
                />
              </label>
              <label>
                起始余量（单位）
                <input
                  required
                  type="number"
                  min="0"
                  step="1"
                  value={form.start_balance}
                  onInput={(e) =>
                    setForm({ ...form, start_balance: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                保存起始余量
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            每次成功提交读数消耗固定 1 单位，余量由服务端按消耗明细重算。
          </p>
        </div>
      )}

      {!isWriter && (
        <div class="card">
          <p class="sub" style={{ marginBottom: 0 }}>
            值班员只读：可查看估算结果与消耗明细，不能修改批次余量。
          </p>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>估算结果</h2>
        <form onSubmit={onQuery}>
          <div class="row">
            <label>
              批次编号
              <input
                value={queryId}
                onInput={(e) => setQueryId(e.target.value)}
                placeholder="输入批次编号查询"
              />
            </label>
            <button type="submit">查询估算</button>
          </div>
        </form>
        {estError && <p class="err">{estError}</p>}
        {!estimate && !estError && (
          <p class="sub" style={{ marginBottom: 0 }}>
            输入批次编号或在下方总览中选择批次，查看服务端估算结果。
          </p>
        )}
        {estimate && (
          <div class="stat-grid">
            <div class="stat">
              <div class="label">批次</div>
              <div class="num">{estimate.batch_id}</div>
            </div>
            <div class="stat">
              <div class="label">起始余量</div>
              <div class="num">{estimate.start_balance}</div>
            </div>
            <div class="stat">
              <div class="label">已消耗单位</div>
              <div class="num">{estimate.consumed_units}</div>
            </div>
            <div class="stat">
              <div class="label">剩余余量（服务端重算）</div>
              <div class={`num ${estimate.remaining <= 2 ? "warn" : ""}`}>
                {estimate.remaining}
              </div>
            </div>
          </div>
        )}
      </div>

      {estimate && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
            消耗明细（批次 {estimate.batch_id}，共 {estimate.entry_count} 条）
          </h2>
          <table>
            <thead>
              <tr>
                <th>明细编号</th>
                <th>读数编号</th>
                <th>消耗单位</th>
                <th>提交人</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {estimate.entries.map((en) => (
                <tr key={en.id}>
                  <td>{en.id}</td>
                  <td>{en.reading_id ?? "—"}</td>
                  <td>{en.units}</td>
                  <td>{en.created_by}</td>
                  <td>{en.created_at ? new Date(en.created_at).toLocaleString() : "—"}</td>
                </tr>
              ))}
              {estimate.entries.length === 0 && (
                <tr>
                  <td colspan="5">暂无消耗明细</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>批次总览</h2>
        <table>
          <thead>
            <tr>
              <th>批次编号</th>
              <th>起始余量</th>
              <th>已消耗</th>
              <th>剩余余量</th>
              <th>明细条数</th>
              <th>维护人</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.batch_id}>
                <td>{b.batch_id}</td>
                <td>{b.start_balance}</td>
                <td>{b.consumed_units}</td>
                <td>{b.remaining}</td>
                <td>{b.entry_count}</td>
                <td>{b.created_by}</td>
                <td>
                  <button
                    type="button"
                    class="secondary"
                    onClick={() => onPick(b.batch_id)}
                  >
                    查看明细
                  </button>
                </td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr>
                <td colspan="7">
                  暂无批次{isWriter ? "，请先在上方维护批次起始余量" : ""}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
