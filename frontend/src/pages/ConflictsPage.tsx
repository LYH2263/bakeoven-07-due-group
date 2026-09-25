import { useEffect, useState } from "react";
import { api } from "../api/client";
type C = { id: number; batch_code: string; oven_id: number; detail: string; created_at: string };
export default function ConflictsPage() {
  const [rows, setRows] = useState<C[]>([]);
  useEffect(() => { api<C[]>("/conflicts").then(setRows); }, []);
  return (<>
    <h2>冲突</h2>
    <table className="table"><thead><tr><th>时间</th><th>批次</th><th>炉位</th><th>详情</th></tr></thead>
    <tbody>{rows.map(c => <tr key={c.id}><td className="mono">{new Date(c.created_at).toLocaleString()}</td><td>{c.batch_code}</td><td>{c.oven_id === 0 ? "—" : c.oven_id}</td><td>{c.detail}</td></tr>)}</tbody></table>
  </>);
}
