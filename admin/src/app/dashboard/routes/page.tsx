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
  const [q, setQ] = useState("");
  const filtered = (data || []).filter(r =>
    !q || r.user_facing_model.toLowerCase().includes(q.toLowerCase())
  );

  async function save(r: R) {
    if (r.id) await api(`/api/v1/admin/routes/${r.id}`, { method: "PUT", body: JSON.stringify(r) });
    else await api(`/api/v1/admin/routes`, { method: "POST", body: JSON.stringify(r) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("Delete this route?")) return;
    await api(`/api/v1/admin/routes/${id}`, { method: "DELETE" }); mutate();
  }
  const modelLabel = (id: number) => models?.find((m) => m.id === id)?.code || `#${id}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Smart routing</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Search public model name" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-primary" onClick={() => setEditing({ ...empty, targets_jsonb: [] })}>New</button>
        </div>
      </div>
      <p className="text-xs text-gray-500">
        strategy: <b>weighted</b> weighted random, <b>fallback</b> ordered fallback by order, <b>smart</b> reserved (currently same as weighted)
      </p>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>Public model</th><th>Strategy</th><th>targets</th><th>Enabled</th><th></th></tr></thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td><td>{r.user_facing_model}</td><td>{r.strategy}</td>
                <td className="text-xs">
                  {r.targets_jsonb.map((t, i) => (
                    <span key={i} className="mr-2">{modelLabel(t.model_id)}(w{t.weight}/o{t.fallback_order})</span>
                  ))}
                </td>
                <td>{r.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...r, targets_jsonb: [...r.targets_jsonb] })}>Edit</button>
                  <button className="btn-danger" onClick={() => del(r.id!)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[640px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "Edit" : "New"} route</h2>
            <div><label className="label">Public model name (the model field users send)</label>
              <input className="input w-full" value={editing.user_facing_model} onChange={(e) => setEditing({ ...editing, user_facing_model: e.target.value })} /></div>
            <div><label className="label">Strategy</label>
              <select className="input w-full" value={editing.strategy} onChange={(e) => setEditing({ ...editing, strategy: e.target.value as any })}>
                <option value="weighted">weighted</option>
                <option value="smart">smart</option>
                <option value="fallback">fallback</option>
              </select></div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="label !mb-0">targets</label>
                <button className="btn-outline text-xs" onClick={() => setEditing({ ...editing, targets_jsonb: [...editing.targets_jsonb, { model_id: models?.[0]?.id || 0, weight: 1, fallback_order: editing.targets_jsonb.length }] })}>+ Add</button>
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
              <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> Enabled
            </label>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={() => save(editing)}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
