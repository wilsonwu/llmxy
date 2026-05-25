"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type P = { id?: number; code: string; name: string; description?: string; price_cents: number; quota_cents: number; duration_days: number; active: boolean };
const empty: P = { code: "", name: "", description: "", price_cents: 0, quota_cents: 0, duration_days: 30, active: true };

export default function PlansPage() {
  const { data, mutate } = useSWR<P[]>("/api/v1/admin/plans", fetcher);
  const [editing, setEditing] = useState<P | null>(null);

  async function save(p: P) {
    if (p.id) await api(`/api/v1/admin/plans/${p.id}`, { method: "PUT", body: JSON.stringify(p) });
    else await api(`/api/v1/admin/plans`, { method: "POST", body: JSON.stringify(p) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("确认删除？")) return;
    await api(`/api/v1/admin/plans/${id}`, { method: "DELETE" }); mutate();
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">套餐</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty })}>新增</button>
      </div>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>名称</th><th>价格</th><th>额度</th><th>有效期</th><th>启用</th><th></th></tr></thead>
          <tbody>
            {data?.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td><td>{p.code}</td><td>{p.name}</td>
                <td>¥{(p.price_cents/100).toFixed(2)}</td>
                <td>¥{(p.quota_cents/100).toFixed(2)}</td>
                <td>{p.duration_days}天</td>
                <td>{p.active ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...p })}>编辑</button>
                  <button className="btn-danger" onClick={() => del(p.id!)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[500px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "编辑" : "新增"}套餐</h2>
            {(["code", "name", "description"] as const).map((k) => (
              <div key={k}><label className="label">{k}</label>
                <input className="input w-full" value={(editing as any)[k] || ""} onChange={(e) => setEditing({ ...editing, [k]: e.target.value })} /></div>
            ))}
            <div className="flex gap-3">
              <div className="flex-1"><label className="label">price (分)</label>
                <input type="number" className="input w-full" value={editing.price_cents} onChange={(e) => setEditing({ ...editing, price_cents: +e.target.value })} /></div>
              <div className="flex-1"><label className="label">quota (分)</label>
                <input type="number" className="input w-full" value={editing.quota_cents} onChange={(e) => setEditing({ ...editing, quota_cents: +e.target.value })} /></div>
              <div className="flex-1"><label className="label">duration (天)</label>
                <input type="number" className="input w-full" value={editing.duration_days} onChange={(e) => setEditing({ ...editing, duration_days: +e.target.value })} /></div>
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> 启用
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
