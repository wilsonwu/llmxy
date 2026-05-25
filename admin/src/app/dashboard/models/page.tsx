"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type M = { id?: number; code: string; display_name: string; channel_id: number; upstream_model: string; prompt_rate: number; completion_rate: number; enabled: boolean };
type C = { id: number; name: string };
const empty: M = { code: "", display_name: "", channel_id: 0, upstream_model: "", prompt_rate: 0, completion_rate: 0, enabled: true };

export default function ModelsPage() {
  const { data, mutate } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const { data: channels } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const [editing, setEditing] = useState<M | null>(null);

  async function save(m: M) {
    if (m.id) await api(`/api/v1/admin/models/${m.id}`, { method: "PUT", body: JSON.stringify(m) });
    else await api(`/api/v1/admin/models`, { method: "POST", body: JSON.stringify(m) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("确认删除？")) return;
    await api(`/api/v1/admin/models/${id}`, { method: "DELETE" }); mutate();
  }
  const chName = (id: number) => channels?.find((c) => c.id === id)?.name || `#${id}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">模型 / 倍率</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty, channel_id: channels?.[0]?.id || 0 })}>新增</button>
      </div>
      <p className="text-xs text-gray-500">rate 单位：微分(1/10000 分) / 1K tokens。例 1500 ≈ $0.00015/1K。</p>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>显示名</th><th>通道</th><th>上游模型</th><th>prompt_rate</th><th>completion_rate</th><th>启用</th><th></th></tr></thead>
          <tbody>
            {data?.map((m) => (
              <tr key={m.id}>
                <td>{m.id}</td><td>{m.code}</td><td>{m.display_name}</td>
                <td>{chName(m.channel_id)}</td><td>{m.upstream_model}</td>
                <td>{m.prompt_rate}</td><td>{m.completion_rate}</td>
                <td>{m.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...m })}>编辑</button>
                  <button className="btn-danger" onClick={() => del(m.id!)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[500px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "编辑" : "新增"}模型</h2>
            <div><label className="label">code (对外暴露名)</label>
              <input className="input w-full" value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value })} /></div>
            <div><label className="label">display_name</label>
              <input className="input w-full" value={editing.display_name} onChange={(e) => setEditing({ ...editing, display_name: e.target.value })} /></div>
            <div><label className="label">channel</label>
              <select className="input w-full" value={editing.channel_id} onChange={(e) => setEditing({ ...editing, channel_id: +e.target.value })}>
                {channels?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select></div>
            <div><label className="label">upstream_model</label>
              <input className="input w-full" value={editing.upstream_model} onChange={(e) => setEditing({ ...editing, upstream_model: e.target.value })} /></div>
            <div className="flex gap-3">
              <div className="flex-1"><label className="label">prompt_rate</label>
                <input type="number" className="input w-full" value={editing.prompt_rate} onChange={(e) => setEditing({ ...editing, prompt_rate: +e.target.value })} /></div>
              <div className="flex-1"><label className="label">completion_rate</label>
                <input type="number" className="input w-full" value={editing.completion_rate} onChange={(e) => setEditing({ ...editing, completion_rate: +e.target.value })} /></div>
              <label className="flex items-center gap-2 pt-5">
                <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> 启用
              </label>
            </div>
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
