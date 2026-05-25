"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type Target = { model_id: number; weight: number; fallback_order: number };
type R = { id?: number; user_facing_model: string; strategy: "weighted" | "smart" | "fallback"; targets_jsonb: Target[]; enabled: boolean };
type M = { id: number; code: string; display_name: string };
const empty: R = { user_facing_model: "", strategy: "weighted", targets_jsonb: [], enabled: true };

export default function RoutesPage() {
  const { data, mutate } = useSWR<R[]>("/api/v1/admin/routes", fetcher);
  const { data: models } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const [editing, setEditing] = useState<R | null>(null);

  async function save(r: R) {
    if (r.id) await api(`/api/v1/admin/routes/${r.id}`, { method: "PUT", body: JSON.stringify(r) });
    else await api(`/api/v1/admin/routes`, { method: "POST", body: JSON.stringify(r) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("确认删除？")) return;
    await api(`/api/v1/admin/routes/${id}`, { method: "DELETE" }); mutate();
  }
  const modelLabel = (id: number) => models?.find((m) => m.id === id)?.code || `#${id}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">智能路由</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty, targets_jsonb: [] })}>新增</button>
      </div>
      <p className="text-xs text-gray-500">
        strategy: <b>weighted</b> 按权重随机, <b>fallback</b> 按 order 顺序兜底, <b>smart</b> 预留 (当前等价 weighted)
      </p>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>对外模型</th><th>策略</th><th>targets</th><th>启用</th><th></th></tr></thead>
          <tbody>
            {data?.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.user_facing_model}</td><td>{r.strategy}</td>
                <td className="text-xs">
                  {r.targets_jsonb.map((t, i) => (
                    <span key={i} className="mr-2">{modelLabel(t.model_id)}(w{t.weight}/o{t.fallback_order})</span>
                  ))}
                </td>
                <td>{r.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...r, targets_jsonb: [...r.targets_jsonb] })}>编辑</button>
                  <button className="btn-danger" onClick={() => del(r.id!)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[640px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "编辑" : "新增"}路由</h2>
            <div><label className="label">对外模型名 (用户调用的 model 字段)</label>
              <input className="input w-full" value={editing.user_facing_model} onChange={(e) => setEditing({ ...editing, user_facing_model: e.target.value })} /></div>
            <div><label className="label">策略</label>
              <select className="input w-full" value={editing.strategy} onChange={(e) => setEditing({ ...editing, strategy: e.target.value as any })}>
                <option value="weighted">weighted</option>
                <option value="smart">smart</option>
                <option value="fallback">fallback</option>
              </select></div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="label !mb-0">targets</label>
                <button className="btn-outline text-xs" onClick={() => setEditing({ ...editing, targets_jsonb: [...editing.targets_jsonb, { model_id: models?.[0]?.id || 0, weight: 1, fallback_order: editing.targets_jsonb.length }] })}>+ 添加</button>
              </div>
              {editing.targets_jsonb.map((t, i) => (
                <div key={i} className="mb-2 flex items-center gap-2">
                  <select className="input flex-1" value={t.model_id} onChange={(e) => {
                    const v = [...editing.targets_jsonb]; v[i] = { ...t, model_id: +e.target.value }; setEditing({ ...editing, targets_jsonb: v });
                  }}>
                    {models?.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.display_name}</option>)}
                  </select>
                  <input className="input w-20" type="number" placeholder="weight" value={t.weight} onChange={(e) => {
                    const v = [...editing.targets_jsonb]; v[i] = { ...t, weight: +e.target.value }; setEditing({ ...editing, targets_jsonb: v });
                  }} />
                  <input className="input w-20" type="number" placeholder="order" value={t.fallback_order} onChange={(e) => {
                    const v = [...editing.targets_jsonb]; v[i] = { ...t, fallback_order: +e.target.value }; setEditing({ ...editing, targets_jsonb: v });
                  }} />
                  <button className="btn-danger" onClick={() => {
                    const v = editing.targets_jsonb.filter((_, j) => j !== i); setEditing({ ...editing, targets_jsonb: v });
                  }}>×</button>
                </div>
              ))}
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> 启用
            </label>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>取消</button>
              <button className="btn-primary" onClick={() => save(editing)}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
