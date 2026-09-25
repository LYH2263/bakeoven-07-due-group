import { useEffect, useState } from "react";
import { api } from "../api/client";
type C = { id: number; batch_code: string; oven_id: number; detail: string; created_at: string };
export default function ConflictsPage() {
  const [rows, setRows] = useState<C[]>([]);
  useEffect(() => { api<C[]>("/conflicts").then(setRows); }, []);
  return (<>
    <h2>冲突</h2>
    <p className="hint">成组定炉失败时只记录一条整组冲突（炉位显示「整组」），批次表与甘特均不写入该组任何一条。</p>
    <table className="table"><thead><tr><th>时间</th><th>批次</th><th>炉位</th><th>详情</th></tr></thead>
    <tbody>{rows.map(c => <tr key={c.id}><td className="mono">{new Date(c.created_at).toLocaleString()}</td><td>{c.batch_code}</td>
      <td>{c.oven_id === 0 ? <span className="conflict-chip conflict-chip--group">整组</span> : c.oven_id}</td>
      <td>{c.detail}</td></tr>)}</tbody></table>
  </>);
}
