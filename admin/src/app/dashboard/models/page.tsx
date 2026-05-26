"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type M = { id?: number; code: string; display_name: string; channel_id: number; upstream_model: string; kind: string; prompt_rate: number; completion_rate: number; enabled: boolean };
type C = { id: number; name: string };
const empty: M = { code: "", display_name: "", channel_id: 0, upstream_model: "", kind: "chat", prompt_rate: 0, completion_rate: 0, enabled: true };

export default function ModelsPage() {
  const { data, mutate } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const { data: channels } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const [editing, setEditing] = useState<M | null>(null);
  const [q, setQ] = useState("");
  const filtered = (data || []).filter(m =>
    !q || m.code.toLowerCase().includes(q.toLowerCase()) || m.upstream_model.toLowerCase().includes(q.toLowerCase()) || m.display_name.toLowerCase().includes(q.toLowerCase())
  );

  async function save(m: M) {
    if (m.id) await api(`/api/v1/admin/models/${m.id}`, { method: "PUT", body: JSON.stringify(m) });
    else await api(`/api/v1/admin/models`, { method: "POST", body: JSON.stringify(m) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("Delete this model?")) return;
    await api(`/api/v1/admin/models/${id}`, { method: "DELETE" }); mutate();
  }
  const chName = (id: number) => channels?.find((c) => c.id === id)?.name || `#${id}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Models / Rates</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Search code/upstream/display name" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-primary" onClick={() => setEditing({ ...empty, channel_id: channels?.[0]?.id || 0 })}>New</button>
        </div>
      </div>
      <p className="text-xs text-gray-500">rate unit: micro-cents (1/10000 cent) / 1K tokens. e.g. 1500 ≈ $0.00015/1K.</p>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>Display name</th><th>Channel</th><th>Upstream model</th><th>Kind</th><th>prompt_rate</th><th>completion_rate</th><th>Enabled</th><th></th></tr></thead>
          <tbody>
            {filtered.map((m) => (
              <tr key={m.id}>
                <td>{m.id}</td><td>{m.code}</td><td>{m.display_name}</td>
                <td>{chName(m.channel_id)}</td><td>{m.upstream_model}</td>
                <td>{m.kind || "chat"}</td>
                <td>{m.prompt_rate}</td><td>{m.completion_rate}</td>
                <td>{m.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...m })}>Edit</button>
                  <button className="btn-danger" onClick={() => del(m.id!)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[500px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "Edit" : "New"} model</h2>
            <div><label className="label">code (public-facing name)</label>
              <input className="input w-full" value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value })} /></div>
            <div><label className="label">display_name</label>
              <input className="input w-full" value={editing.display_name} onChange={(e) => setEditing({ ...editing, display_name: e.target.value })} /></div>
            <div><label className="label">channel</label>
              <select className="input w-full" value={editing.channel_id} onChange={(e) => setEditing({ ...editing, channel_id: +e.target.value })}>
                {channels?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select></div>
            <div><label className="label">upstream_model</label>
              <input className="input w-full" value={editing.upstream_model} onChange={(e) => setEditing({ ...editing, upstream_model: e.target.value })} /></div>
            <div><label className="label">kind</label>
              <select className="input w-full" value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value })}>
                <option value="chat">chat (default — chat/completions)</option>
                <option value="embedding">embedding (for smart-route classifier)</option>
              </select>
              <p className="text-xs text-gray-500 mt-1">Embedding models are used by smart routing to classify prompts; they cannot be exposed as user-facing chat models.</p>
            </div>
            <div className="flex gap-3">
              <div className="flex-1"><label className="label">prompt_rate</label>
                <input type="number" className="input w-full" value={editing.prompt_rate} onChange={(e) => setEditing({ ...editing, prompt_rate: +e.target.value })} /></div>
              <div className="flex-1"><label className="label">completion_rate</label>
                <input type="number" className="input w-full" value={editing.completion_rate} onChange={(e) => setEditing({ ...editing, completion_rate: +e.target.value })} /></div>
              <label className="flex items-center gap-2 pt-5">
                <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} /> Enabled
              </label>
            </div>
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
