"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";
import { Badge, EmptyState, IconButton, ListActions, ListCell, ListMeta, Modal, TableSkeleton, useToast } from "@/components/ui";

type C = { id?: number; name: string; provider_type: string; base_url: string; api_key_enc?: string; enabled: boolean };

const empty: C = { name: "", provider_type: "openai", base_url: "https://api.openai.com/v1", api_key_enc: "", enabled: true };

const UPSTREAM_PROTOCOLS = [
  { id: "openai", label: "OpenAI-compatible", hint: "OpenAI, DeepSeek, Moonshot, Qwen, Together, and other /v1-compatible APIs" },
  { id: "azure", label: "Azure OpenAI", hint: "Azure OpenAI deployment endpoints" },
  { id: "anthropic", label: "Anthropic", hint: "Claude Messages API" },
  { id: "gemini", label: "Google Gemini", hint: "Gemini generateContent / embedding APIs" },
];

function protocolMeta(id: string) {
  return UPSTREAM_PROTOCOLS.find((p) => p.id === id) || { id, label: id, hint: "Custom upstream adapter" };
}

function protocolTone(id: string) {
  return id === "anthropic" ? "purple" : id === "gemini" ? "warning" : id === "azure" ? "brand" : id === "openai" ? "success" : "neutral";
}

export default function ChannelsPage() {
  const { data, mutate, isLoading } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const [editing, setEditing] = useState<C | null>(null);
  const [q, setQ] = useState("");
  const { toast, confirm } = useToast();
  const filtered = (data || []).filter(c =>
    !q || c.name.toLowerCase().includes(q.toLowerCase()) || c.base_url.toLowerCase().includes(q.toLowerCase())
  );

  async function save(c: C) {
    try {
      if (c.id) await api(`/api/v1/admin/channels/${c.id}`, { method: "PUT", body: JSON.stringify(c) });
      else await api(`/api/v1/admin/channels`, { method: "POST", body: JSON.stringify(c) });
      setEditing(null);
      mutate();
      toast(c.id ? "Channel updated" : "Channel created", "success");
    } catch (e: any) {
      toast(e?.message || "Save failed", "error");
    }
  }
  async function del(id: number, name: string) {
    if (!(await confirm({ title: "Delete channel", body: `Delete "${name}"? Models bound to it will lose their upstream.`, danger: true, confirmText: "Delete" }))) return;
    try {
      await api(`/api/v1/admin/channels/${id}`, { method: "DELETE" });
      mutate();
      toast("Channel deleted", "success");
    } catch (e: any) {
      toast(e?.message || "Delete failed", "error");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Upstream channels</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Search name/URL" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search channels" />
          <button className="btn-primary" onClick={() => setEditing({ ...empty })}>New</button>
        </div>
      </div>
      <div className="card overflow-x-auto p-0">
        <table className="table">
          <thead><tr><th>Channel</th><th>Upstream</th><th>Endpoint</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {isLoading && <TableSkeleton cols={5} />}
            {!isLoading && filtered.map((c) => (
              <tr key={c.id}>
                <td>
                  <ListCell
                    primary={<span>{c.name}</span>}
                    secondary={<ListMeta>#{c.id}</ListMeta>}
                  />
                </td>
                <td>
                  <ListCell
                    primary={<Badge tone={protocolTone(c.provider_type)}>{protocolMeta(c.provider_type).label}</Badge>}
                    secondary={protocolMeta(c.provider_type).hint}
                  />
                </td>
                <td>
                  <ListCell
                    primary={<code className="break-all font-mono text-xs text-gray-800">{c.base_url}</code>}
                    secondary={<ListMeta>adapter {c.provider_type}</ListMeta>}
                  />
                </td>
                <td>
                  <ListCell
                    primary={c.enabled ? <Badge tone="success">on</Badge> : <Badge tone="neutral">off</Badge>}
                    secondary={c.enabled ? "available for model routing" : "hidden from routing"}
                  />
                </td>
                <td>
                  <ListActions>
                    <IconButton label={`Edit ${c.name}`} icon="edit" onClick={() => setEditing({ ...c, api_key_enc: c.api_key_enc || "" })} />
                    <IconButton label={`Delete ${c.name}`} icon="delete" tone="danger" onClick={() => del(c.id!, c.name)} />
                  </ListActions>
                </td>
              </tr>
            ))}
            {!isLoading && !filtered.length && (
              <tr><td colSpan={5}><EmptyState title={q ? "No channels match your search" : "No channels yet"} hint={q ? undefined : "Create one to point at an upstream provider like OpenAI or Azure."} /></td></tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`${editing?.id ? "Edit" : "New"} channel`}
        footer={
          <>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary" onClick={() => editing && save(editing)}>Save</button>
          </>
        }
      >
        {editing && (
          <>
            <div>
              <label className="label">Name</label>
              <input className="input w-full" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </div>
            <div>
              <label className="label">Upstream protocol</label>
              <select className="input w-full" value={editing.provider_type} onChange={(e) => setEditing({ ...editing, provider_type: e.target.value })}>
                {UPSTREAM_PROTOCOLS.map((p) => <option key={p.id} value={p.id}>{p.label} - {p.hint}</option>)}
              </select>
              <p className="mt-1 text-xs text-gray-500">Models can inherit this protocol or override it per model. This value selects the upstream adapter and wire format.</p>
            </div>
            <div>
              <label className="label">Base URL</label>
              <input className="input w-full" placeholder={editing.provider_type === "anthropic" ? "https://api.anthropic.com" : editing.provider_type === "gemini" ? "https://generativelanguage.googleapis.com" : editing.provider_type === "azure" ? "https://{resource}.openai.azure.com" : "https://api.openai.com/v1"} value={editing.base_url} onChange={(e) => setEditing({ ...editing, base_url: e.target.value })} />
            </div>
            <div>
              <label className="label">API Key</label>
              <input className="input w-full" type="password" value={editing.api_key_enc || ""} onChange={(e) => setEditing({ ...editing, api_key_enc: e.target.value })} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} />
              Enabled
            </label>
          </>
        )}
      </Modal>
    </div>
  );
}
