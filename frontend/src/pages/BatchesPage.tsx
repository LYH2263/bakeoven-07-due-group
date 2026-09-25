import { useEffect, useState } from "react";
import { api } from "../api/client";
type P = { id: number; name: string }; type O = { id: number; label: string };
type B = { id: number; code: string; product_name?: string; oven_label?: string; start_min: number; ferment_end?: number; bake_end?: number; status: string };
type GroupRow = { product_id: number | ""; start_min: number; due_min: number; code: string };
type Placement = {
  submitted_index: number; code: string; product_id: number; product_name?: string;
  oven_id: number; oven_label?: string; start_min: number; ferment_end: number; bake_end: number;
};
type GroupError = {
  detail: {
    message: string; order_index: number; submitted_index: number;
    batch_code: string; due_min: number;
    ovens: { oven_id: number; oven_label: string; earliest_end_min: number | null }[];
  };
};
function fmt(m: number) { const h = Math.floor(m/60), mm = m%60; return `${String(h).padStart(2,"0")}:${String(mm).padStart(2,"0")}`; }
function blankRow(pid: number | "", start: number): GroupRow { return { product_id: pid, start_min: start, due_min: start + 60, code: "" }; }

export default function BatchesPage() {
  const [products, setProducts] = useState<P[]>([]);
  const [ovens, setOvens] = useState<O[]>([]);
  const [rows, setRows] = useState<B[]>([]);
  const [pid, setPid] = useState<number | "">(""); const [oid, setOid] = useState<number | "">("");
  const [start, setStart] = useState(11 * 60); const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [groupRows, setGroupRows] = useState<GroupRow[]>([]);
  const [groupMsg, setGroupMsg] = useState<Placement[]>([]);
  const [groupErr, setGroupErr] = useState<GroupError["detail"] | null>(null);
  const [groupBusy, setGroupBusy] = useState(false);
  const reload = () => api<B[]>("/batches").then(setRows);
  useEffect(() => {
    api<P[]>("/products").then(p => { setProducts(p); if (p[0]) { setPid(p[0].id); setGroupRows([blankRow(p[0].id, 8 * 60)]); } });
    api<O[]>("/ovens").then(o => { setOvens(o); if (o[0]) setOid(o[0].id); });
    reload();
  }, []);
  async function create() {
    setMsg(""); setErr("");
    try {
      const b = await api<B>("/batches", { method: "POST", body: JSON.stringify({ product_id: pid, oven_id: oid, start_min: start }) });
      setMsg(`已排产 ${b.code}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  function updateGroupRow(i: number, patch: Partial<GroupRow>) {
    setGroupRows(rs => rs.map((r, j) => j === i ? { ...r, ...patch } : r));
  }
  async function submitGroup() {
    setGroupMsg([]); setGroupErr(null);
    if (!groupRows.length) { setGroupErr({ message: "至少添加一条", order_index: -1, submitted_index: -1, batch_code: "", due_min: 0, ovens: [] }); return; }
    for (const [i, r] of groupRows.entries()) {
      if (r.product_id === "") return setGroupErr({ message: `第 ${i + 1} 条未选产品`, order_index: -1, submitted_index: i, batch_code: "", due_min: r.due_min, ovens: [] });
      if (r.due_min < r.start_min) return setGroupErr({ message: `第 ${i + 1} 条应出炉早于开工`, order_index: -1, submitted_index: i, batch_code: r.code, due_min: r.due_min, ovens: [] });
    }
    setGroupBusy(true);
    try {
      const res = await api<{ placements: Placement[] }>("/batches/group", {
        method: "POST",
        body: JSON.stringify({
          items: groupRows.map(r => ({
            product_id: r.product_id, start_min: r.start_min, due_min: r.due_min,
            ...(r.code.trim() ? { code: r.code.trim() } : {}),
          })),
        }),
      });
      setGroupMsg(res.placements);
      reload();
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e);
      try { setGroupErr((JSON.parse(text) as GroupError).detail); } catch { setGroupErr({ message: text, order_index: -1, submitted_index: -1, batch_code: "", due_min: 0, ovens: [] }); }
    } finally { setGroupBusy(false); }
  }
  return (<>
    <h2>批次</h2>
    <h3>成组定炉（按应出炉）</h3>
    <p className="hint">一组批次按应出炉从早到晚依次定炉：每座炉取不早于开工的最早空档，选烘烤结束最早的炉；任一条无炉可排则整组不写入。</p>
    <table className="table group-editor">
      <thead><tr><th>#</th><th>产品</th><th>开工分钟</th><th>应出炉分钟</th><th>编码(可选)</th><th></th></tr></thead>
      <tbody>
        {groupRows.map((r, i) => (
          <tr key={i}>
            <td>{i + 1}</td>
            <td><select value={r.product_id} onChange={e => updateGroupRow(i, { product_id: Number(e.target.value) })}>
              {products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select></td>
            <td><input type="number" value={r.start_min} onChange={e => updateGroupRow(i, { start_min: Number(e.target.value) })} style={{ width: 90 }} />
              <span className="mono dim"> {fmt(Math.max(0, r.start_min))}</span></td>
            <td><input type="number" value={r.due_min} onChange={e => updateGroupRow(i, { due_min: Number(e.target.value) })} style={{ width: 90 }} />
              <span className="mono dim"> {fmt(Math.max(0, r.due_min))}</span></td>
            <td><input value={r.code} onChange={e => updateGroupRow(i, { code: e.target.value })} placeholder="自动" style={{ width: 110 }} /></td>
            <td><button onClick={() => setGroupRows(rs => rs.filter((_, j) => j !== i))}>删除</button></td>
          </tr>
        ))}
        {!groupRows.length && <tr><td colSpan={6} className="dim">尚未添加批次</td></tr>}
      </tbody>
    </table>
    <div className="toolbar">
      <button onClick={() => setGroupRows(rs => [...rs, blankRow(pid === "" ? "" : pid, 8 * 60)])}>＋ 添加一条</button>
      <button onClick={submitGroup} disabled={groupBusy}>{groupBusy ? "排产中…" : "整组提交定炉"}</button>
    </div>
    {groupMsg.length > 0 && (
      <div className="ok">
        <div>整组 {groupMsg.length} 条已排产，甘特已更新：</div>
        <table className="table"><thead><tr><th>批次</th><th>产品</th><th>分到炉位</th><th>发酵</th><th>烘烤结束</th></tr></thead>
          <tbody>{groupMsg.map(p => <tr key={p.submitted_index}>
            <td className="mono">{p.code}</td><td>{p.product_name}</td><td>{p.oven_label}</td>
            <td className="mono">{fmt(p.start_min)}–{fmt(p.ferment_end)}</td>
            <td className="mono">{fmt(p.bake_end)}</td>
          </tr>)}</tbody></table>
      </div>
    )}
    {groupErr && (
      <div className="err group-error">
        {groupErr.order_index >= 0 ? (<>
          <div><strong>整组未写入</strong>：卡在按应出炉处理的第 {groupErr.order_index + 1} 条
            {groupErr.batch_code && <>（编码 {groupErr.batch_code}，提交序号 {groupErr.submitted_index + 1}）</>}，
            应出炉 <strong>{groupErr.due_min} 分（{fmt(Math.max(0, groupErr.due_min))}）</strong> 前没有炉能烤完。</div>
          <table className="table"><thead><tr><th>炉位</th><th>最早能结束（分钟）</th></tr></thead>
            <tbody>{groupErr.ovens.map(o => <tr key={o.oven_id}>
              <td>{o.oven_label}</td>
              <td className="mono">{o.earliest_end_min === null ? "当日放不下" : `${o.earliest_end_min}（${fmt(o.earliest_end_min)}）`}</td>
            </tr>)}</tbody></table>
        </>) : <div>{groupErr.message}</div>}
      </div>
    )}

    <h3>单条排产（手工指定炉位）</h3>
    <div className="toolbar">
      <select value={pid} onChange={e => setPid(Number(e.target.value))}>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <select value={oid} onChange={e => setOid(Number(e.target.value))}>{ovens.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <label>开工分钟 <input type="number" value={start} onChange={e => setStart(Number(e.target.value))} style={{ width: 90 }} /></label>
      <button onClick={create}>创建生产批次</button>
    </div>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <table className="table"><thead><tr><th>批次</th><th>产品</th><th>炉位</th><th>发酵</th><th>烘烤结束</th><th>状态</th></tr></thead>
    <tbody>{rows.map(b => <tr key={b.id}><td className="mono">{b.code}</td><td>{b.product_name}</td><td>{b.oven_label}</td>
      <td className="mono">{fmt(b.start_min)}–{fmt(b.ferment_end ?? b.start_min)}</td>
      <td className="mono">{fmt(b.bake_end ?? b.start_min)}</td><td>{b.status}</td></tr>)}</tbody></table>
  </>);
}
