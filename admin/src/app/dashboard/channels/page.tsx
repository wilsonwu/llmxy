"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";
import { Badge, EmptyState, IconButton, ListActions, ListCell, ListMeta, Modal, TableSkeleton, useToast } from "@/components/ui";

type C = { id?: number; name: string; provider_type: string; connector_type: string; base_url: string; api_key_enc?: string; enabled: boolean };

const empty: C = { name: "", provider_type: "openai", connector_type: "openai", base_url: "https://api.openai.com/v1", api_key_enc: "", enabled: true };

const UPSTREAM_PROTOCOLS = [
  { id: "openai", label: "OpenAI", hint: "OpenAI chat/completions, embeddings, and image response semantics" },
  { id: "anthropic", label: "Anthropic", hint: "Claude Messages API semantics" },
  { id: "gemini", label: "Gemini", hint: "Google Gemini generateContent / embedding semantics" },
];

const UPSTREAM_CONNECTORS = [
  { id: "openai", protocol: "openai", label: "OpenAI-compatible", hint: "Bearer auth with /v1 paths; works for OpenAI and compatible gateways", baseUrl: "https://api.openai.com/v1" },
  { id: "azure_openai", protocol: "openai", label: "Azure OpenAI", hint: "Azure deployment paths, api-key header, and api-version query", baseUrl: "https://{resource}.openai.azure.com" },
  { id: "anthropic", protocol: "anthropic", label: "Anthropic", hint: "x-api-key auth with /v1/messages", baseUrl: "https://api.anthropic.com" },
  { id: "gemini", protocol: "gemini", label: "Google Gemini", hint: "key query parameter with Gemini REST paths", baseUrl: "https://generativelanguage.googleapis.com" },
];

function protocolMeta(id: string) {
  return UPSTREAM_PROTOCOLS.find((p) => p.id === id) || { id, label: id, hint: "Custom upstream adapter" };
}

function connectorMeta(id: string) {
  return UPSTREAM_CONNECTORS.find((p) => p.id === id) || { id, protocol: "openai", label: id, hint: "Custom upstream connector", baseUrl: "" };
}

function connectorsForProtocol(protocol: string) {
  return UPSTREAM_CONNECTORS.filter((c) => c.protocol === protocol);
}

function defaultConnector(protocol: string) {
  return connectorsForProtocol(protocol)[0]?.id || "openai";
}

function protocolTone(id: string) {
  return id === "anthropic" ? "purple" : id === "gemini" ? "warning" : id === "openai" ? "success" : "neutral";
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
                    primary={<><Badge tone={protocolTone(c.provider_type)}>{protocolMeta(c.provider_type).label}</Badge><Badge tone="neutral">{connectorMeta(c.connector_type).label}</Badge></>}
                    secondary={connectorMeta(c.connector_type).hint}
                  />
                </td>
                <td>
                  <ListCell
                    primary={<code className="break-all font-mono text-xs text-gray-800">{c.base_url}</code>}
                    secondary={<ListMeta>connector {c.connector_type}</ListMeta>}
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
              <label className="label">Semantic protocol</label>
              <select className="input w-full" value={editing.provider_type} onChange={(e) => {
                const protocol = e.target.value;
                const connector = defaultConnector(protocol);
                setEditing({ ...editing, provider_type: protocol, connector_type: connector, base_url: connectorMeta(connector).baseUrl || editing.base_url });
              }}>
                {UPSTREAM_PROTOCOLS.map((p) => <option key={p.id} value={p.id}>{p.label} - {p.hint}</option>)}
              </select>
              <p className="mt-1 text-xs text-gray-500">This is the upstream request/response shape. Models can inherit it or override it per model.</p>
            </div>
            <div>
              <label className="label">Connection type</label>
              <select className="input w-full" value={editing.connector_type} onChange={(e) => {
                const connector = e.target.value;
                setEditing({ ...editing, connector_type: connector, base_url: connectorMeta(connector).baseUrl || editing.base_url });
              }}>
                {connectorsForProtocol(editing.provider_type).map((p) => <option key={p.id} value={p.id}>{p.label} - {p.hint}</option>)}
              </select>
              <p className="mt-1 text-xs text-gray-500">This selects URL rules, authentication headers, path templates, and API-version handling.</p>
            </div>
            <div>
              <label className="label">Base URL</label>
              <input className="input w-full" placeholder={connectorMeta(editing.connector_type).baseUrl || "https://api.openai.com/v1"} value={editing.base_url} onChange={(e) => setEditing({ ...editing, base_url: e.target.value })} />
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
