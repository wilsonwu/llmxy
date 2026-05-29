"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type Tier = { size: string; quality: string; price_micro: number };
type Pricing = { mode?: string; tiers?: Tier[]; default_price_micro?: number };
type M = { id?: number; code: string; display_name: string; channel_id: number; upstream_model: string; kind: string; upstream_protocol?: string | null; prompt_rate: number; completion_rate: number; pricing_jsonb: Pricing; enabled: boolean };
type C = { id: number; name: string };
const CHAT_PROTOCOLS = ["openai", "azure", "anthropic", "gemini"];
// Anthropic has no embeddings API, so it's excluded from embedding models.
const EMBEDDING_PROTOCOLS = ["openai", "azure", "gemini"];
const IMAGE_PROTOCOLS = ["openai", "azure", "gemini"];
const empty: M = { code: "", display_name: "", channel_id: 0, upstream_model: "", kind: "chat", upstream_protocol: null, prompt_rate: 0, completion_rate: 0, pricing_jsonb: {}, enabled: true };

// A representative upstream request for each (protocol, kind), mirroring the
// adapters in api/app/services/providers. Lets admins eyeball whether the
// selected protocol produces the wire format they expect. `m` is the upstream
// model name (Azure treats it as the deployment name).
function upstreamSample(protocol: string, kind: string, m: string): string | null {
  const model = m || "<upstream_model>";
  const apiVer = "2024-10-21";
  const imgVer = "2025-04-01-preview";
  if (protocol === "openai") {
    if (kind === "embedding")
      return [
        "POST {base_url}/v1/embeddings",
        "Authorization: Bearer <api-key>",
        "",
        JSON.stringify({ model, input: "Hello world" }, null, 2),
      ].join("\n");
    if (kind === "image")
      return [
        "POST {base_url}/v1/images/generations",
        "Authorization: Bearer <api-key>",
        "",
        JSON.stringify({ model, prompt: "a red panda coding", n: 1, size: "1024x1024" }, null, 2),
      ].join("\n");
    return [
      "POST {base_url}/v1/chat/completions",
      "Authorization: Bearer <api-key>",
      "",
      JSON.stringify({ model, messages: [{ role: "user", content: "Hello" }], stream: false }, null, 2),
    ].join("\n");
  }
  if (protocol === "azure") {
    // Azure puts the deployment in the URL and strips body.model; auth is api-key.
    if (kind === "embedding")
      return [
        `POST {base_url}/openai/deployments/${model}/embeddings?api-version=${apiVer}`,
        "api-key: <api-key>",
        "",
        JSON.stringify({ input: "Hello world" }, null, 2),
      ].join("\n");
    if (kind === "image")
      return [
        `POST {base_url}/openai/deployments/${model}/images/generations?api-version=${imgVer}`,
        "api-key: <api-key>",
        "",
        JSON.stringify({ prompt: "a red panda coding", n: 1, size: "1024x1024" }, null, 2),
      ].join("\n");
    return [
      `POST {base_url}/openai/deployments/${model}/chat/completions?api-version=${apiVer}`,
      "api-key: <api-key>",
      "",
      JSON.stringify({ messages: [{ role: "user", content: "Hello" }], stream: false }, null, 2),
    ].join("\n");
  }
  if (protocol === "anthropic") {
    // Chat only — Anthropic has no embeddings/image APIs.
    return [
      "POST {base_url}/v1/messages",
      "x-api-key: <api-key>",
      "anthropic-version: 2023-06-01",
      "",
      JSON.stringify(
        { model, max_tokens: 1024, messages: [{ role: "user", content: "Hello" }], stream: false },
        null,
        2,
      ),
    ].join("\n");
  }
  if (protocol === "gemini") {
    if (kind === "embedding")
      return [
        `POST {base_url}/v1beta/models/${model}:batchEmbedContents?key=<api-key>`,
        "",
        JSON.stringify(
          { requests: [{ model: `models/${model}`, content: { parts: [{ text: "Hello world" }] } }] },
          null,
          2,
        ),
      ].join("\n");
    if (kind === "image") return null; // Gemini image generation not yet supported
    return [
      `POST {base_url}/v1beta/models/${model}:generateContent?key=<api-key>`,
      "",
      JSON.stringify({ contents: [{ role: "user", parts: [{ text: "Hello" }] }] }, null, 2),
    ].join("\n");
  }
  return null;
}

// Micro-cents → human dollar string. 1 micro-cent = 1/10000 cent.
function microToUsd(micro: number): string {
  return `$${(micro / 1_000_000).toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
}

// Kind-aware short pricing summary for the model list.
function pricingSummary(m: M) {
  if (m.kind === "image") {
    const p = m.pricing_jsonb || {};
    const tiers = p.tiers || [];
    const def = p.default_price_micro ?? 0;
    if (!tiers.length) return <span className="text-xs">default {microToUsd(def)}/img</span>;
    const prices = tiers.map((t) => t.price_micro);
    const lo = Math.min(...prices), hi = Math.max(...prices);
    return (
      <span className="text-xs">
        {tiers.length} tier{tiers.length > 1 ? "s" : ""}{" "}
        {lo === hi ? microToUsd(lo) : `${microToUsd(lo)}–${microToUsd(hi)}`}/img
        <span className="text-gray-400"> · def {microToUsd(def)}</span>
      </span>
    );
  }
  // chat / embedding: per-1K-token rates (completion is irrelevant for embedding)
  return (
    <span className="text-xs">
      p {m.prompt_rate}
      {m.kind !== "embedding" && <> · c {m.completion_rate}</>}
    </span>
  );
}

export default function ModelsPage() {
  const { data, mutate } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const { data: channels } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const [editing, setEditing] = useState<M | null>(null);
  const [q, setQ] = useState("");
  const [showSample, setShowSample] = useState(false);
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
      <p className="text-xs text-gray-500">token rate unit: micro-cents (1/10000 cent) / 1K tokens (chat/embedding). image priced per generated image. e.g. 1500 ≈ $0.0015/1K · 400000 = $0.40/img.</p>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>Display name</th><th>Channel</th><th>Upstream model</th><th>Kind</th><th>Protocol</th><th>Pricing</th><th>Enabled</th><th></th></tr></thead>
          <tbody>
            {filtered.map((m) => (
              <tr key={m.id}>
                <td>{m.id}</td><td>{m.code}</td><td>{m.display_name}</td>
                <td>{chName(m.channel_id)}</td><td>{m.upstream_model}</td>
                <td>{m.kind || "chat"}</td>
                <td className="text-xs text-gray-500">{m.upstream_protocol || "auto"}</td>
                <td>{pricingSummary(m)}</td>
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
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30 p-4">
          <div className="card w-[560px] max-h-[90vh] space-y-4 overflow-y-auto">
            <h2 className="text-lg font-semibold">{editing.id ? "Edit" : "New"} model</h2>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="label">channel</label>
                <select className="input w-full" value={editing.channel_id} onChange={(e) => setEditing({ ...editing, channel_id: +e.target.value })}>
                  {channels?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select></div>
              <div><label className="label">upstream_model</label>
                <input className="input w-full" value={editing.upstream_model} onChange={(e) => setEditing({ ...editing, upstream_model: e.target.value })} /></div>
              <div><label className="label">kind</label>
                <select className="input w-full" value={editing.kind} onChange={(e) => setEditing({ ...editing, kind: e.target.value })}>
                  <option value="chat">chat (chat/completions)</option>
                  <option value="embedding">embedding (embeddings)</option>
                  <option value="image">image (images/generations)</option>
                </select></div>
              <div><label className="label">upstream protocol</label>
                <select className="input w-full" value={editing.upstream_protocol || ""} onChange={(e) => setEditing({ ...editing, upstream_protocol: e.target.value || null })}>
                  <option value="">(auto — channel default)</option>
                  {(editing.kind === "image" ? IMAGE_PROTOCOLS : editing.kind === "embedding" ? EMBEDDING_PROTOCOLS : CHAT_PROTOCOLS).map((p) => <option key={p} value={p}>{p}</option>)}
                </select></div>
              <div><label className="label">code (public-facing name)</label>
                <input className="input w-full" value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value })} /></div>
              <div><label className="label">display_name</label>
                <input className="input w-full" value={editing.display_name} onChange={(e) => setEditing({ ...editing, display_name: e.target.value })} /></div>
            </div>
            <p className="text-xs text-gray-500">Protocol selects the upstream translation adapter; one channel can host mixed protocols (e.g. Azure AI Foundry). Leave on auto to use the channel&apos;s provider type.</p>

            {/* Collapsible upstream request preview keeps the modal short by default */}
            <div className="rounded border border-gray-200">
              <button type="button" className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50"
                onClick={() => setShowSample((s) => !s)}>
                <span>Upstream request preview {editing.upstream_protocol ? `(${editing.upstream_protocol})` : "(select a protocol)"}</span>
                <span>{showSample ? "▾" : "▸"}</span>
              </button>
              {showSample && (
                <div className="px-3 pb-3">
                  {editing.upstream_protocol ? (() => {
                    const sample = upstreamSample(editing.upstream_protocol, editing.kind, editing.upstream_model);
                    return sample ? (
                      <pre className="text-[11px] leading-relaxed bg-gray-900 text-gray-100 rounded p-3 overflow-x-auto whitespace-pre-wrap break-all">{sample}</pre>
                    ) : (
                      <p className="text-xs text-amber-600">⚠ {editing.upstream_protocol} does not support {editing.kind} (no adapter / not yet implemented).</p>
                    );
                  })() : (
                    <p className="text-xs text-gray-400">Pick a protocol to preview the exact upstream request format LLMxY sends after translating the incoming OpenAI-format call.</p>
                  )}
                </div>
              )}
            </div>

            {editing.kind === "image" ? (
              <ImagePricingEditor pricing={editing.pricing_jsonb || {}} onChange={(p) => setEditing({ ...editing, pricing_jsonb: p })} />
            ) : (
              <div className="flex gap-3">
                <div className="flex-1"><label className="label">prompt_rate</label>
                  <input type="number" className="input w-full" value={editing.prompt_rate} onChange={(e) => setEditing({ ...editing, prompt_rate: +e.target.value })} /></div>
                {editing.kind !== "embedding" && (
                  <div className="flex-1"><label className="label">completion_rate</label>
                    <input type="number" className="input w-full" value={editing.completion_rate} onChange={(e) => setEditing({ ...editing, completion_rate: +e.target.value })} /></div>
                )}
              </div>
            )}
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

function ImagePricingEditor({ pricing, onChange }: { pricing: Pricing; onChange: (p: Pricing) => void }) {
  const tiers: Tier[] = pricing.tiers || [];
  const def = pricing.default_price_micro ?? 0;

  function setTiers(next: Tier[]) {
    onChange({ ...pricing, mode: "per_image", tiers: next, default_price_micro: def });
  }
  function setDefault(v: number) {
    onChange({ ...pricing, mode: "per_image", tiers, default_price_micro: v });
  }
  function addTier() {
    setTiers([...tiers, { size: "1024x1024", quality: "standard", price_micro: 0 }]);
  }
  function updTier(i: number, patch: Partial<Tier>) {
    setTiers(tiers.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  }
  function delTier(i: number) {
    setTiers(tiers.filter((_, idx) => idx !== i));
  }

  return (
    <div className="space-y-2 rounded border border-gray-200 p-3">
      <div className="flex items-center justify-between">
        <label className="label">Image pricing (per generated image)</label>
        <button className="btn-outline" onClick={addTier}>Add tier</button>
      </div>
      <p className="text-xs text-gray-500">price unit: micro-cents (1/10000 cent) per image. e.g. 400000 = 40 cents = $0.40. The pre-deduction uses the matching tier (size + quality); unmatched requests use the most expensive tier, refunded after generation.</p>
      <table className="table">
        <thead><tr><th>size</th><th>quality</th><th>price_micro</th><th></th></tr></thead>
        <tbody>
          {tiers.map((t, i) => (
            <tr key={i}>
              <td><input className="input w-full" value={t.size} onChange={(e) => updTier(i, { size: e.target.value })} placeholder="1024x1024" /></td>
              <td><input className="input w-full" value={t.quality} onChange={(e) => updTier(i, { quality: e.target.value })} placeholder="standard" /></td>
              <td><input type="number" className="input w-full" value={t.price_micro} onChange={(e) => updTier(i, { price_micro: +e.target.value })} /></td>
              <td><button className="btn-danger" onClick={() => delTier(i)}>×</button></td>
            </tr>
          ))}
          {tiers.length === 0 && <tr><td colSpan={4} className="text-xs text-gray-400">No tiers — only the default price will be used.</td></tr>}
        </tbody>
      </table>
      <div><label className="label">default_price_micro (fallback when no tier matches)</label>
        <input type="number" className="input w-full" value={def} onChange={(e) => setDefault(+e.target.value)} /></div>
    </div>
  );
}
