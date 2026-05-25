"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type C = { id?: number; name: string; provider_type: string; base_url: string; api_key_enc?: string; enabled: boolean; priority: number; weight: number };

const empty: C = { name: "", provider_type: "openai", base_url: "https://api.openai.com/v1", api_key_enc: "", enabled: true, priority: 100, weight: 1 };

const PROVIDERS = [
  { id: "openai", label: "OpenAI 兼容 (OpenAI/DeepSeek/Moonshot/通义...)" },
  { id: "azure", label: "Azure OpenAI" },
  { id: "anthropic", label: "Anthropic (Claude)" },
  { id: "gemini", label: "Google Gemini" },
];

export default function ChannelsPage() {
  const { data, mutate } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const [editing, setEditing] = useState<C | null>(null);

  async function save(c: C) {
    if (c.id) await api(`/api/v1/admin/channels/${c.id}`, { method: "PUT", body: JSON.stringify(c) });
    else await api(`/api/v1/admin/channels`, { method: "POST", body: JSON.stringify(c) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("确认删除？")) return;
    await api(`/api/v1/admin/channels/${id}`, { method: "DELETE" }); mutate();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">上游通道</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty })}>新增</button>
      </div>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>名称</th><th>类型</th><th>BaseURL</th><th>权重</th><th>启用</th><th></th></tr></thead>
          <tbody>
            {data?.map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td><td>{c.name}</td><td>{c.provider_type}</td><td className="font-mono text-xs">{c.base_url}</td>
                <td>{c.weight}</td><td>{c.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...c, api_key_enc: c.api_key_enc || "" })}>编辑</button>
                  <button className="btn-danger" onClick={() => del(c.id!)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[500px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "编辑" : "新增"}通道</h2>
            <div>
              <label className="label">名称</label>
              <input className="input w-full" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </div>
            <div>
              <label className="label">上游协议</label>
              <select className="input w-full" value={editing.provider_type} onChange={(e) => setEditing({ ...editing, provider_type: e.target.value })}>
                {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Base URL</label>
              <input className="input w-full" placeholder={editing.provider_type === "anthropic" ? "https://api.anthropic.com" : editing.provider_type === "gemini" ? "https://generativelanguage.googleapis.com" : editing.provider_type === "azure" ? "https://{resource}.openai.azure.com" : "https://api.openai.com/v1"} value={editing.base_url} onChange={(e) => setEditing({ ...editing, base_url: e.target.value })} />
            </div>
            <div>
              <label className="label">API Key</label>
              <input className="input w-full" type="password" value={editing.api_key_enc || ""} onChange={(e) => setEditing({ ...editing, api_key_enc: e.target.value })} />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="label">priority</label>
                <input type="number" className="input w-full" value={editing.priority} onChange={(e) => setEditing({ ...editing, priority: +e.target.value })} />
              </div>
              <div className="flex-1">
                <label className="label">weight</label>
                <input type="number" className="input w-full" value={editing.weight} onChange={(e) => setEditing({ ...editing, weight: +e.target.value })} />
              </div>
              <label className="flex items-center gap-2 pt-5">
                <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} />
                启用
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
