import { useEffect, useState } from "react";
import { api } from "../api/client";
type Call = { id: number; floor: number; direction: string; passengers: number; status: string; score: string; assigned_car_id: number | null };
export default function DispatchPage() {
  const [rows, setRows] = useState<Call[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const waiting = rows.filter(r => r.status === "waiting");
  const reload = () => api<Call[]>("/calls").then(r => { setRows(r); setPicked(new Set()); });
  useEffect(() => { reload(); }, []);
  async function run(id: number) {
    setMsg(""); setErr("");
    try {
      const c = await api<Call>("/dispatch", { method: "POST", body: JSON.stringify({ call_id: id }) });
      setMsg(`呼梯 #${c.id} → 轿厢 ${c.assigned_car_id}，评分 ${c.score}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); reload(); }
  }
  async function runBatch() {
    setMsg(""); setErr("");
    const ids = waiting.filter(c => picked.has(c.id)).map(c => c.id);
    try {
      const list = await api<Call[]>("/dispatch/batch", { method: "POST", body: JSON.stringify({ call_ids: ids }) });
      setMsg(`联派成功：${list.map(c => `#${c.id}→轿厢${c.assigned_car_id}`).join("，")}（全部已 assigned）`);
      reload();
    } catch (e) {
      // 整批失败：后端未写库，列表刷新后这些呼梯仍为 waiting、轿厢载荷不变
      setErr(e instanceof Error ? e.message : String(e));
      reload();
    }
  }
  function toggle(id: number) {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  return (<>
    <h2>派工</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    <div className="toolbar">
      <button onClick={runBatch} disabled={picked.size < 2}>联派所选（{picked.size}）</button>
      <span className="dispatch-deck-hint">勾选多笔 waiting 呼梯后一次提交：全部成功才落库，任一无可用轿厢则整批保持 waiting</span>
    </div>
    <table className="table"><thead><tr><th></th><th>呼梯</th><th>楼层</th><th>方向</th><th>人数</th><th></th></tr></thead>
    <tbody>{waiting.map(c => <tr key={c.id}>
      <td><input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} /></td>
      <td>#{c.id}</td><td>{c.floor}</td><td>{c.direction}</td><td>{c.passengers}</td>
      <td><button onClick={() => run(c.id)}>评分派轿厢</button></td></tr>)}
      {!waiting.length && <tr><td colSpan={6}>暂无待派呼梯</td></tr>}
    </tbody></table>
  </>);
}
