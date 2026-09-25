import { useEffect, useState } from "react";
import { api } from "../api/client";
type P = { id: number; name: string }; type O = { id: number; label: string };
type B = { id: number; code: string; product_name?: string; oven_label?: string; start_min: number; ferment_end?: number; bake_end?: number; status: string };
type GItem = { product_id: number | ""; start_min: number; due_min: number };
function fmt(m: number) { const h = Math.floor(m/60), mm = m%60; return `${String(h).padStart(2,"0")}:${String(mm).padStart(2,"0")}`; }
export default function BatchesPage() {
  const [products, setProducts] = useState<P[]>([]);
  const [ovens, setOvens] = useState<O[]>([]);
  const [rows, setRows] = useState<B[]>([]);
  const [pid, setPid] = useState<number | "">(""); const [oid, setOid] = useState<number | "">("");
  const [start, setStart] = useState(11 * 60); const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [group, setGroup] = useState<GItem[]>([]);
  const reload = () => api<B[]>("/batches").then(setRows);
  useEffect(() => {
    api<P[]>("/products").then(p => { setProducts(p); if (p[0]) setPid(p[0].id); });
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
  function addGroupRow() {
    setGroup(g => [...g, { product_id: products[0]?.id ?? "", start_min: 8 * 60, due_min: 10 * 60 }]);
  }
  function setGroupRow(i: number, patch: Partial<GItem>) {
    setGroup(g => g.map((it, j) => (j === i ? { ...it, ...patch } : it)));
  }
  async function submitGroup() {
    setMsg(""); setErr("");
    try {
      const created = await api<B[]>("/batches/assign-group", {
        method: "POST",
        body: JSON.stringify({ items: group.map(g => ({ product_id: g.product_id, start_min: g.start_min, due_min: g.due_min })) }),
      });
      setMsg(`成组定炉成功：${created.map(b => `${b.code}→${b.oven_label}`).join("、")}`);
      setGroup([]);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }
  return (<>
    <h2>批次</h2>
    <div className="toolbar">
      <select value={pid} onChange={e => setPid(Number(e.target.value))}>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <select value={oid} onChange={e => setOid(Number(e.target.value))}>{ovens.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
      <label>开工分钟 <input type="number" value={start} onChange={e => setStart(Number(e.target.value))} style={{ width: 90 }} /></label>
      <button onClick={create}>创建生产批次</button>
    </div>
    <h3>成组定炉（按应出炉）</h3>
    <div className="toolbar">
      <button onClick={addGroupRow}>加一行</button>
      <button onClick={submitGroup} disabled={!group.length}>成组定炉</button>
      {group.length > 0 && <span className="mono">{group.length} 条待提交</span>}
    </div>
    {group.length > 0 && (
      <table className="table"><thead><tr><th>#</th><th>产品</th><th>开工分钟</th><th>应出炉分钟</th><th /></tr></thead>
      <tbody>{group.map((g, i) => <tr key={i}>
        <td className="mono">{i + 1}</td>
        <td><select value={g.product_id} onChange={e => setGroupRow(i, { product_id: Number(e.target.value) })}>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></td>
        <td><input type="number" value={g.start_min} onChange={e => setGroupRow(i, { start_min: Number(e.target.value) })} style={{ width: 90 }} /></td>
        <td><input type="number" value={g.due_min} onChange={e => setGroupRow(i, { due_min: Number(e.target.value) })} style={{ width: 90 }} /></td>
        <td><button onClick={() => setGroup(gs => gs.filter((_, j) => j !== i))}>删除</button></td>
      </tr>)}</tbody></table>
    )}
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <table className="table"><thead><tr><th>批次</th><th>产品</th><th>炉位</th><th>发酵</th><th>烘烤结束</th><th>状态</th></tr></thead>
    <tbody>{rows.map(b => <tr key={b.id}><td className="mono">{b.code}</td><td>{b.product_name}</td><td>{b.oven_label}</td>
      <td className="mono">{fmt(b.start_min)}–{fmt(b.ferment_end ?? b.start_min)}</td>
      <td className="mono">{fmt(b.bake_end ?? b.start_min)}</td><td>{b.status}</td></tr>)}</tbody></table>
  </>);
}
