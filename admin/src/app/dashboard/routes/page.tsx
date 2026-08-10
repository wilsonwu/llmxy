"use client";
import useSWR from "swr";
import { useEffect, useState } from "react";
import { api, fetcher } from "@/lib/api";
import { COUNTRIES, COUNTRY_NAME } from "@/lib/countries";
import { Badge, EmptyState, IconButton, ListActions, ListCell, ListMeta, Modal, TableSkeleton, useToast } from "@/components/ui";

type Target = { model_id: number; weight: number; label?: string | null };
type Rule =
  | { type: "preset"; id: string; label: string }
  | { type: "tokens"; threshold: number; gt_label: string; lte_label: string }
  | { type: "keyword"; pattern: string; label: string }
  | { type: "code_block"; label: string }
  | { type: "geo"; countries: string[]; label: string };
type Exemplar = { label: string; text: string };
type ExposedProtocol = "openai.chat" | "openai.responses" | "anthropic.messages" | "openai.embeddings" | "openai.images";
type R = {
  id?: number;
  user_facing_model: string;
  strategy: "weighted" | "round_robin" | "smart";
  targets_jsonb: Target[];
  fallback_model_id?: number | null;
  smart_rules_jsonb?: Rule[];
  smart_embedding_model_id?: number | null;
  smart_exemplars_jsonb?: Exemplar[];
  smart_score_threshold?: number;
  scope: "public" | "private";
  enabled: boolean;
  // Client-facing modality this route serves. Upstream wire protocol is
  // selected automatically from each target model's adapter configuration.
  modality: "chat" | "embedding" | "image";
  exposed_protocols?: ExposedProtocol[];
};
type M = { id: number; code: string; display_name: string; channel_id: number; upstream_model: string; kind?: string; upstream_protocol?: string | null; enabled?: boolean };
type C = { id: number; name: string; provider_type: string; connector_type: string; enabled?: boolean };

const empty: R = {
  user_facing_model: "",
  strategy: "weighted",
  targets_jsonb: [],
  fallback_model_id: null,
  smart_rules_jsonb: [],
  smart_embedding_model_id: null,
  smart_exemplars_jsonb: [],
  smart_score_threshold: 55,
  scope: "public",
  enabled: true,
  modality: "chat",
  exposed_protocols: ["openai.chat"],
};

const STRATEGY_DESC: Record<R["strategy"], { title: string; body: string }> = {
  weighted: {
    title: "weighted — weighted random split",
    body: "Pick one primary target by weighted random sampling. Optionally retry one selected fallback model if that primary fails.",
  },
  round_robin: {
    title: "round robin — ordered rotation",
    body: "Rotate through available targets in configuration order. API-direct and Envoy share the cursor; optional fallback retry behaves like weighted routing.",
  },
  smart: {
    title: "smart — pick by prompt content",
    body:
      "Pick one deciding mechanism: rules or an embedding classifier. A resolved label selects one weighted target; unmatched requests use the default target group. Fallback retry is optional.",
  },
};

const PRESETS: { id: string; title: string; hint: string; label: string }[] = [
  { id: "code_block", title: "Contains code block (```)", hint: "Prompt has ``` fences — programming/code review tasks", label: "code" },
  { id: "long_prompt", title: "Long prompt (~>800 tokens)", hint: "Long context — usually needs stronger model", label: "long" },
  { id: "short_prompt", title: "Short prompt (~≤80 tokens)", hint: "Tiny question — cheap model fine", label: "short" },
  { id: "translate", title: "Translation request", hint: "Mentions translate / translation", label: "translate" },
  { id: "math", title: "Math / calculation", hint: "Mentions solve / equation / proof / LaTeX markers", label: "math" },
  { id: "reasoning", title: "Reasoning / step-by-step", hint: "Mentions step-by-step / chain of thought", label: "reasoning" },
  { id: "summarize", title: "Summarization", hint: "Mentions summarize / tl;dr / summary", label: "summarize" },
  { id: "creative", title: "Creative writing", hint: "Mentions story / poem / novel", label: "creative" },
  { id: "chinese", title: "Chinese (CJK ≥30%)", hint: "Mostly Chinese characters", label: "chinese" },
  { id: "english", title: "English-only", hint: "Almost no CJK characters", label: "english" },
];

const PRESET_BY_ID = Object.fromEntries(PRESETS.map((p) => [p.id, p]));

const DEFAULT_LABEL = "default";

export default function RoutesPage() {
  const { data, mutate, isLoading } = useSWR<R[]>("/api/v1/admin/routes", fetcher);
  const { data: models } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const { data: channels } = useSWR<C[]>("/api/v1/admin/channels", fetcher);
  const embeddingModels = (models || []).filter((m) => m.kind === "embedding");
  const [editing, setEditing] = useState<R | null>(null);
  const editingKey = editing ? (editing.id ?? "new") : "closed";
  const [extraSmartLabels, setExtraSmartLabels] = useState<string[]>([]);
  const [newSmartLabel, setNewSmartLabel] = useState("");
  const [q, setQ] = useState("");
  const { toast, confirm } = useToast();
  const filtered = (data || []).filter(r => !q || r.user_facing_model.toLowerCase().includes(q.toLowerCase()));

  useEffect(() => {
    setExtraSmartLabels([]);
    setNewSmartLabel("");
  }, [editingKey]);

  async function save(r: R) {
    const payload: R = { ...r };
    const protocols = routeProtocols(payload);
    payload.exposed_protocols = payload.modality === "chat" ? protocols : [payload.modality === "image" ? "openai.images" : "openai.embeddings"];
    if (r.strategy !== "smart") {
      payload.smart_rules_jsonb = [];
      payload.smart_embedding_model_id = null;
      payload.smart_exemplars_jsonb = [];
    } else {
      payload.targets_jsonb = payload.targets_jsonb.map((t) => ({
        ...t,
        label: (t.label || "").trim() || null,
      }));
    }
    try {
      if (r.id) await api(`/api/v1/admin/routes/${r.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await api(`/api/v1/admin/routes`, { method: "POST", body: JSON.stringify(payload) });
      setEditing(null);
      mutate();
      toast(r.id ? "Route updated" : "Route created", "success");
    } catch (e: any) { toast(e?.message || "Save failed", "error"); }
  }
  async function del(id: number, name: string) {
    if (!(await confirm({ title: "Delete route", body: `Delete route "${name}"? Clients calling this public model name will start to fail.`, danger: true, confirmText: "Delete" }))) return;
    try {
      await api(`/api/v1/admin/routes/${id}`, { method: "DELETE" });
      mutate();
      toast("Route deleted", "success");
    } catch (e: any) { toast(e?.message || "Delete failed", "error"); }
  }
  const modelById = (id: number) => models?.find((m) => m.id === id);
  const channelById = (id: number) => channels?.find((c) => c.id === id);
  const modelLabel = (id: number) => modelById(id)?.code || `#${id}`;
  const modalityTone = (m: string) => m === "image" ? "purple" : m === "embedding" ? "brand" : "info";
  const protocolTone = (p: string) => p.startsWith("anthropic.") ? "purple" : p.startsWith("gemini.") ? "warning" : p.startsWith("openai.") ? "success" : "neutral";
  const protocolLabel = (p: string) => p.replace(".", " / ");
  const connectorLabel = (c: string) => c === "azure_openai" ? "azure openai" : c === "openai" ? "openai-compatible" : c;
  function routeProtocols(r: R): ExposedProtocol[] {
    const raw = r.exposed_protocols?.length ? r.exposed_protocols : ["openai.chat"];
    const normalized = Array.from(new Set(raw.map((p) => normalizeExposedProtocol(p, r.modality)).filter((p): p is ExposedProtocol => Boolean(p))));
    return normalized.length ? normalized : ["openai.chat"];
  }

  function normalizeExposedProtocol(protocol: string, modality: R["modality"]): ExposedProtocol {
    if (protocol === "openai") return modality === "image" ? "openai.images" : modality === "embedding" ? "openai.embeddings" : "openai.chat";
    if (protocol === "anthropic") return "anthropic.messages";
    if (protocol === "openai.chat" || protocol === "openai.responses" || protocol === "anthropic.messages" || protocol === "openai.embeddings" || protocol === "openai.images") return protocol;
    return "openai.chat";
  }
  const effectiveProtocol = (m?: M) => {
    if (!m) return "unknown";
    return (m.upstream_protocol || channelById(m.channel_id)?.provider_type || "channel default").toLowerCase();
  };
  const effectiveConnector = (m?: M) => {
    if (!m) return "unknown";
    return (channelById(m.channel_id)?.connector_type || "openai").toLowerCase();
  };
  const modelOptionLabel = (m: M) => {
    const disabled = m.enabled === false ? " · disabled" : "";
    return `${m.code} — ${m.display_name} · upstream ${protocolLabel(effectiveProtocol(m))} via ${connectorLabel(effectiveConnector(m))} · ${m.upstream_model}${disabled}`;
  };

  const targetLabel = (target: Target) => (target.label || "").trim() || DEFAULT_LABEL;

  const renderTargetChip = (target: Target, text: string, key: string | number) => {
    const model = modelById(target.model_id);
    const protocol = effectiveProtocol(model);
    const connector = effectiveConnector(model);
    return (
      <span
        key={key}
        className="inline-flex max-w-[14rem] items-center rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] leading-4 text-gray-700"
        title={model ? `Upstream protocol: ${protocol}; connector: ${connector}; model: ${model.upstream_model}` : undefined}
      >
        <span className="truncate">{text}</span>
      </span>
    );
  };

  const renderTargetSummary = (r: R) => {
    if (!r.targets_jsonb.length) return <span className="text-gray-400">no targets</span>;
    if (r.strategy === "smart") {
      const groups = new Map<string, Target[]>();
      for (const target of r.targets_jsonb) {
        const label = targetLabel(target);
        groups.set(label, [...(groups.get(label) || []), target]);
      }
      const labels = Array.from(groups.keys()).sort((a, b) => (
        a === DEFAULT_LABEL ? -1 : b === DEFAULT_LABEL ? 1 : a.localeCompare(b)
      ));
      return (
        <div className="flex min-w-0 flex-col gap-1">
          {labels.map((label) => (
            <div key={label} className="flex min-w-0 flex-wrap items-center gap-1">
              <Badge tone={label === DEFAULT_LABEL ? "neutral" : "info"} className="max-w-[9rem] truncate font-mono">{label}</Badge>
              {(groups.get(label) || []).map((target, index) => renderTargetChip(target, `${modelLabel(target.model_id)} · w${target.weight}`, `${label}-${index}`))}
            </div>
          ))}
          {r.fallback_model_id != null && (
            <div className="flex min-w-0 flex-wrap items-center gap-1">
              <Badge tone="warning">fallback</Badge>
              {renderTargetChip(
                { model_id: r.fallback_model_id, weight: 0 },
                modelLabel(r.fallback_model_id),
                "fallback",
              )}
            </div>
          )}
        </div>
      );
    }
    return (
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 flex-wrap gap-1">
          {r.targets_jsonb.map((target, index) => renderTargetChip(
            target,
            r.strategy === "round_robin"
              ? `${index + 1}. ${modelLabel(target.model_id)}`
              : `${modelLabel(target.model_id)} · w${target.weight}`,
            index,
          ))}
        </div>
        {r.fallback_model_id != null && (
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <Badge tone="warning">fallback</Badge>
            {renderTargetChip(
              { model_id: r.fallback_model_id, weight: 0 },
              modelLabel(r.fallback_model_id),
              "fallback",
            )}
          </div>
        )}
      </div>
    );
  };

  const targetPrimary = (r: R) => {
    const count = r.targets_jsonb.length;
    if (r.strategy !== "smart") return `${count} target${count === 1 ? "" : "s"}`;
    const labelCount = new Set(r.targets_jsonb.map(targetLabel)).size;
    return `${count} target${count === 1 ? "" : "s"} / ${labelCount} label${labelCount === 1 ? "" : "s"}`;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Smart routing</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Search public model name" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-primary" onClick={() => setEditing({ ...empty, targets_jsonb: [], modality: "chat" })}>New</button>
        </div>
      </div>

      <div className="card grid gap-2 text-xs md:grid-cols-3">
        {(Object.keys(STRATEGY_DESC) as R["strategy"][]).map((s) => (
          <div key={s} className="rounded border p-2">
            <div className="mb-1 font-semibold">{STRATEGY_DESC[s].title}</div>
            <div className="text-gray-600">{STRATEGY_DESC[s].body}</div>
          </div>
        ))}
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="table">
          <thead>
            <tr><th>Route</th><th>Policy</th><th>Targets</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {isLoading && <TableSkeleton cols={5} />}
            {!isLoading && filtered.map((r) => (
              <tr key={r.id}>
                <td>
                  <ListCell
                    primary={<span>{r.user_facing_model}</span>}
                    secondary={<><ListMeta>#{r.id}</ListMeta><Badge tone={modalityTone(r.modality || "chat")}>{r.modality || "chat"}</Badge>{routeProtocols(r).map((p) => <Badge key={p} tone={protocolTone(p)}>{p}</Badge>)}</>}
                  />
                </td>
                <td>
                  <ListCell
                    primary={<span>{r.strategy}</span>}
                    secondary={
                      <>
                        <Badge tone={r.scope === "private" ? "warning" : "success"}>{r.scope}</Badge>
                      </>
                    }
                  />
                </td>
                <td>
                  <ListCell
                    primary={<span>{targetPrimary(r)}</span>}
                    secondary={renderTargetSummary(r)}
                  />
                </td>
                <td>
                  {r.enabled ? <Badge tone="success">on</Badge> : <Badge tone="neutral">off</Badge>}
                </td>
                <td>
                  <ListActions>
                    <IconButton label={`Edit ${r.user_facing_model}`} icon="edit" onClick={() => setEditing({
                      ...r,
                      targets_jsonb: [...r.targets_jsonb],
                      smart_rules_jsonb: [...(r.smart_rules_jsonb || [])],
                      smart_exemplars_jsonb: [...(r.smart_exemplars_jsonb || [])],
                      smart_score_threshold: r.smart_score_threshold ?? 55,
                      modality: r.modality || "chat",
                      exposed_protocols: routeProtocols(r),
                    })} />
                    <IconButton label={`Delete ${r.user_facing_model}`} icon="delete" tone="danger" onClick={() => del(r.id!, r.user_facing_model)} />
                  </ListActions>
                </td>
              </tr>
            ))}
            {!isLoading && !filtered.length && (
              <tr><td colSpan={5}><EmptyState title={q ? "No routes match your search" : "No routes yet"} hint={q ? undefined : "Create one to expose a public model name backed by one or more upstream models."} /></td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editing && (() => {
        const e = editing;
        const exemplars = e.smart_exemplars_jsonb || [];

        // Active labels in this route are sourced from rules, exemplars, targets, and the implicit default.
        const ruleLabels: string[] = Array.from(new Set([
          ...(e.smart_rules_jsonb || []).flatMap((r: any) => {
            if (r.type === "tokens") return [r.gt_label, r.lte_label];
            return [r.label];
          }).filter((s: any): s is string => typeof s === "string" && s.trim().length > 0),
        ]));
        const exemplarLabels: string[] = Array.from(new Set(
          exemplars.map((x) => x.label).filter((s) => typeof s === "string" && s.trim().length > 0),
        ));
        const targetLabels: string[] = e.targets_jsonb
          .map(targetLabel)
          .filter((s) => s.length > 0);
        const allLabels: string[] = Array.from(new Set([DEFAULT_LABEL, ...ruleLabels, ...exemplarLabels, ...targetLabels, ...extraSmartLabels]));

        const nextLabel = (prefix: string): string => {
          const taken = new Set((e.smart_rules_jsonb || []).map((r: any) => r.label).filter(Boolean));
          for (let n = 1; n < 999; n++) {
            const cand = `${prefix}_${n}`;
            if (!taken.has(cand)) return cand;
          }
          return `${prefix}_x`;
        };

        const addPreset = (pid: string) => {
          if (!pid) return;
          let rule: Rule;
          if (pid === "__custom_keyword") {
            rule = { type: "keyword", pattern: "", label: nextLabel("kw") };
          } else if (pid === "__custom_geo") {
            rule = { type: "geo", countries: [], label: nextLabel("geo") };
          } else {
            const meta = PRESET_BY_ID[pid];
            if (!meta) return;
            rule = { type: "preset", id: pid, label: meta.label };
          }
          setEditing({ ...e, smart_rules_jsonb: [...(e.smart_rules_jsonb || []), rule] });
        };

        const LabelSelect = ({ value, onChange, options, w = "w-full", emptyText = "— pick label —" }:
          { value: string; onChange: (v: string) => void; options: string[]; w?: string; emptyText?: string }) => (
          <select className={`input min-w-0 max-w-full ${w}`} value={value} onChange={(ev) => onChange(ev.target.value)}>
            <option value="">{emptyText}</option>
            {options.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        );

        const availableTargetModels = models?.filter((m) => (m.kind || "chat") === (e.modality || "chat")) || [];
        const firstTargetModelId = availableTargetModels[0]?.id || 0;
        const storedTargetLabel = (label: string) => label === DEFAULT_LABEL ? null : label;
        const makeTarget = (label?: string): Target => ({
          model_id: firstTargetModelId,
          weight: 1,
          label: e.strategy === "smart" && label ? storedTargetLabel(label) : null,
        });
        const labelForTarget = targetLabel;
        const addSmartLabel = () => {
          const label = newSmartLabel.trim();
          if (!label) return;
          setExtraSmartLabels((prev) => prev.includes(label) ? prev : [...prev, label]);
          setNewSmartLabel("");
        };
        const renderTargetMeta = (selected?: M) => {
          if (!selected) return null;
          const channel = channelById(selected.channel_id);
          const protocol = effectiveProtocol(selected);
          const isModelOverride = Boolean(selected.upstream_protocol);
          return (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span>auto upstream</span>
              <Badge tone={protocolTone(protocol)}>{protocol}</Badge>
              <span>upstream <code>{selected.upstream_model}</code></span>
              <span>channel {channel?.name || `#${selected.channel_id}`}</span>
              <span>{isModelOverride ? "from model adapter" : "from channel default"}</span>
            </div>
          );
        };

        const targetRowClass = "grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_5rem_auto] sm:items-center";

        const renderTargets = () => (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="label !mb-0">Targets</label>
              <button className="btn-outline text-xs" onClick={() => setEditing({
                ...e,
                targets_jsonb: [...e.targets_jsonb, makeTarget()],
              })}>+ Add</button>
            </div>
            {e.targets_jsonb.map((t, i) => {
              const selected = modelById(t.model_id);
              return (
                <div key={i} className="mb-2 rounded border border-gray-200 bg-white p-2">
                  <div className={targetRowClass}>
                    <select className="input w-full min-w-0" value={t.model_id} onChange={(ev) => {
                      const v = [...e.targets_jsonb]; v[i] = { ...t, model_id: +ev.target.value };
                      setEditing({ ...e, targets_jsonb: v });
                    }}>
                      {models?.filter((m) => (m.kind || "chat") === (e.modality || "chat")).map((m) => (
                        <option key={m.id} value={m.id}>{modelOptionLabel(m)}</option>
                      ))}
                    </select>
                    {e.strategy === "weighted" && (
                      <input className="input w-full min-w-0" type="number" min={0} placeholder="weight" value={t.weight}
                        onChange={(ev) => {
                          const v = [...e.targets_jsonb]; v[i] = { ...t, weight: +ev.target.value };
                          setEditing({ ...e, targets_jsonb: v });
                        }} />
                    )}
                    <button className="btn-danger w-full sm:w-auto" onClick={() => {
                      const v = e.targets_jsonb.filter((_, j) => j !== i);
                      setEditing({
                        ...e,
                        targets_jsonb: v,
                        fallback_model_id: v.length ? e.fallback_model_id : null,
                      });
                    }}>×</button>
                  </div>
                  {renderTargetMeta(selected)}
                </div>
              );
            })}
          </div>
        );

        const renderSmartTargets = () => {
          const smartLabels = allLabels;
          const addModelToLabel = (label: string) => setEditing({
            ...e,
            targets_jsonb: [...e.targets_jsonb, makeTarget(label)],
          });
          return (
            <div>
              <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <label className="label !mb-0">Targets by label</label>
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 sm:w-[360px]">
                  <input
                    className="input min-w-0"
                    placeholder="new label"
                    value={newSmartLabel}
                    onChange={(ev) => setNewSmartLabel(ev.target.value)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter") {
                        ev.preventDefault();
                        addSmartLabel();
                      }
                    }}
                  />
                  <button className="btn-outline text-xs" onClick={addSmartLabel}>Add label</button>
                </div>
              </div>
              <div className="space-y-3">
                {smartLabels.map((label) => {
                  const group = e.targets_jsonb
                    .map((target, index) => ({ target, index }))
                    .filter(({ target }) => labelForTarget(target) === label);
                  const canRemoveLabel = label !== DEFAULT_LABEL && group.length === 0 && extraSmartLabels.includes(label);
                  return (
                    <div key={label} className="rounded border border-gray-200 bg-white p-3">
                      <div className="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <div className="min-w-0">
                          <span className="inline-flex max-w-full items-center rounded bg-gray-100 px-2 py-0.5 font-mono text-xs text-gray-700">
                            <span className="truncate">{label}</span>
                          </span>
                          <span className="ml-2 text-xs text-gray-500">{group.length} model{group.length === 1 ? "" : "s"}</span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {canRemoveLabel && (
                            <button className="btn-ghost text-xs" onClick={() => setExtraSmartLabels((prev) => prev.filter((x) => x !== label))}>Remove label</button>
                          )}
                          <button className="btn-outline text-xs" onClick={() => addModelToLabel(label)}>+ Add model</button>
                        </div>
                      </div>
                      {group.length === 0 ? (
                        <p className="text-xs text-gray-400">No target models.</p>
                      ) : (
                        <div className="space-y-2">
                          {group.map(({ target, index }) => {
                            const selected = modelById(target.model_id);
                            return (
                              <div key={index} className="rounded border border-gray-100 bg-gray-50 p-2">
                                <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_5rem_auto] sm:items-center">
                                  <select className="input w-full min-w-0" value={target.model_id} onChange={(ev) => {
                                    const next = [...e.targets_jsonb];
                                    next[index] = { ...target, model_id: +ev.target.value, label: storedTargetLabel(label) };
                                    setEditing({ ...e, targets_jsonb: next });
                                  }}>
                                    {availableTargetModels.map((m) => (
                                      <option key={m.id} value={m.id}>{modelOptionLabel(m)}</option>
                                    ))}
                                  </select>
                                  <input className="input w-full min-w-0" type="number" min={0} placeholder="weight" value={target.weight}
                                    onChange={(ev) => {
                                      const next = [...e.targets_jsonb];
                                      next[index] = { ...target, weight: +ev.target.value, label: storedTargetLabel(label) };
                                      setEditing({ ...e, targets_jsonb: next });
                                    }} />
                                  <button className="btn-danger w-full sm:w-auto" onClick={() => {
                                    const next = e.targets_jsonb.filter((_, j) => j !== index);
                                    setEditing({
                                      ...e,
                                      targets_jsonb: next,
                                      fallback_model_id: next.length ? e.fallback_model_id : null,
                                    });
                                  }}>×</button>
                                </div>
                                {renderTargetMeta(selected)}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        };

        const renderFallbackControl = () => (
          <div className="rounded border border-gray-200 bg-gray-50 p-3">
            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                checked={e.fallback_model_id != null}
                disabled={!firstTargetModelId || e.targets_jsonb.length === 0}
                onChange={(ev) => setEditing({
                  ...e,
                  fallback_model_id: ev.target.checked ? firstTargetModelId : null,
                })}
              />
              Retry a fallback model
            </label>
            {e.fallback_model_id != null && (
              <div className="mt-3">
                <label className="label">Fallback model</label>
                <select
                  className="input w-full"
                  value={e.fallback_model_id}
                  onChange={(ev) => setEditing({ ...e, fallback_model_id: +ev.target.value })}
                >
                  {availableTargetModels.map((model) => (
                    <option key={model.id} value={model.id}>{modelOptionLabel(model)}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500">
                  Any model of this API type can be selected, including a target model. It is skipped when already selected as the primary.
                </p>
              </div>
            )}
          </div>
        );

        return (
        <Modal
          open={!!editing}
          onClose={() => setEditing(null)}
          title={`${e.id ? "Edit" : "New"} route`}
          width="w-[760px]"
          footer={
            <>
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={() => save(e)}>Save</button>
            </>
          }
        >
            <div>
              <label className="label">Public model name</label>
              <input className="input w-full" value={e.user_facing_model}
                onChange={(ev) => setEditing({ ...e, user_facing_model: ev.target.value })} />
            </div>

            <div>
              <label className="label">Client API type</label>
              <select className="input w-full" value={e.modality || "chat"}
                onChange={(ev) => {
                  const mod = ev.target.value as R["modality"];
                  setEditing({ ...e, modality: mod, targets_jsonb: [], fallback_model_id: null, exposed_protocols: mod === "chat" ? routeProtocols(e).filter((p) => p === "openai.chat" || p === "openai.responses" || p === "anthropic.messages") : [mod === "image" ? "openai.images" : "openai.embeddings"] });
                }}>
                <option value="chat">chat (text generation)</option>
                <option value="embedding">embedding</option>
                <option value="image">image</option>
              </select>
              <p className="mt-1 text-xs text-gray-500">
                This only chooses the public API shape clients call. Target models choose the upstream semantic protocol; their channels choose the connector, such as OpenAI-compatible or Azure OpenAI. Switching API type clears the current targets.
              </p>
            </div>

            <div>
              <label className="label">Public protocols</label>
              <div className="flex flex-wrap gap-2">
                {(["openai.chat", "openai.responses", "anthropic.messages"] as ExposedProtocol[]).map((p) => {
                  const checked = routeProtocols(e).includes(p);
                  const disabled = (e.modality || "chat") !== "chat";
                  return (
                    <label key={p} className={`flex items-center gap-2 rounded border px-3 py-2 text-sm ${disabled ? "bg-gray-50 text-gray-400" : "bg-white"}`}>
                      <input
                        type="checkbox"
                        checked={checked && !disabled}
                        disabled={disabled}
                        onChange={(ev) => {
                          const current = routeProtocols(e);
                          const next = ev.target.checked ? [...current, p] : current.filter((x) => x !== p);
                          setEditing({ ...e, exposed_protocols: next.length ? Array.from(new Set(next)) : ["openai.chat"] });
                        }}
                      />
                      <span>{protocolLabel(p)}</span>
                    </label>
                  );
                })}
              </div>
              <p className="mt-1 text-xs text-gray-500">
                These are the client-facing request and response protocols. Target models still use their own upstream adapters automatically.
              </p>
            </div>

            <div>
              <label className="label">Strategy</label>
              <select className="input w-full" value={e.strategy}
                onChange={(ev) => {
                  const strategy = ev.target.value as R["strategy"];
                  setEditing({ ...e, strategy });
                }}>
                <option value="weighted">weighted</option>
                <option value="round_robin">round robin</option>
                <option value="smart">smart</option>
              </select>
              <p className="mt-1 text-xs text-gray-500">{STRATEGY_DESC[e.strategy].body}</p>
              {e.targets_jsonb.length <= 1 && e.fallback_model_id == null && (
                <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
                  Only one target — strategy is effectively a no-op. Add more targets to make {e.strategy} meaningful.
                </p>
              )}
            </div>

            {e.strategy !== "smart" && renderTargets()}

            {e.strategy !== "smart" && renderFallbackControl()}

            {e.strategy === "smart" && (
              <div className="space-y-4 rounded border bg-gray-50 p-3">
                <div className="text-sm font-semibold">Smart routing decision</div>
                <p className="text-xs text-gray-500">
                  Pick <b>one</b> deciding mechanism — rules <i>or</i> embedding classifier. It emits a <i>label</i> assigned to targets below. Unmatched requests use the <code>default</code> group.
                </p>

                {(() => {
                  const smartMode: "rules" | "embedding" = e.smart_embedding_model_id ? "embedding" : "rules";
                  const switchMode = (m: "rules" | "embedding") => {
                    if (m === smartMode) return;
                    if (m === "rules") {
                      setEditing({
                        ...e,
                        smart_embedding_model_id: null,
                        smart_exemplars_jsonb: [],
                      });
                    } else {
                      setEditing({
                        ...e,
                        smart_rules_jsonb: [],
                        smart_embedding_model_id: embeddingModels[0]?.id ?? null,
                      });
                    }
                  };
                  return (
                    <div className="space-y-1">
                      <div className="inline-flex overflow-hidden rounded border bg-white text-xs">
                        <button
                          className={`px-3 py-1 ${smartMode === "rules" ? "bg-blue-600 text-white" : "text-gray-700"}`}
                          onClick={() => switchMode("rules")}>Rules</button>
                        <button
                          className={`px-3 py-1 ${
                            smartMode === "embedding"
                              ? "bg-blue-600 text-white"
                              : embeddingModels.length === 0
                                ? "cursor-not-allowed text-gray-300"
                                : "text-gray-700"
                          }`}
                          onClick={() => switchMode("embedding")}
                          disabled={embeddingModels.length === 0}
                          title={embeddingModels.length === 0 ? "Register an embedding model first (Models page → kind=embedding)" : ""}>
                          Embedding classifier
                        </button>
                      </div>
                      {embeddingModels.length === 0 && (
                        <p className="text-xs text-amber-700">
                          Embedding classifier is disabled because no embedding model is registered. Add one on the{" "}
                          <a href="/dashboard/models" className="font-medium underline">Models</a> page with{" "}
                          <code>kind=embedding</code> (e.g. <code>text-embedding-3-small</code>), then it becomes selectable here.
                        </p>
                      )}
                    </div>
                  );
                })()}

                {/* ---- Rules block ---- */}
                {!e.smart_embedding_model_id && (
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="label !mb-0">Rules (ordered, first match wins)</label>
                    <select
                      className="input text-xs"
                      value=""
                      onChange={(ev) => { addPreset(ev.target.value); ev.target.value = ""; }}>
                      <option value="">+ Add rule…</option>
                      {PRESETS.map((p) => (
                        <option key={p.id} value={p.id}>{p.title} → {p.label}</option>
                      ))}
                      <option value="__custom_keyword">Custom: keyword/regex</option>
                      <option value="__custom_geo">Geo (by country)</option>
                    </select>
                  </div>

                  {(e.smart_rules_jsonb || []).length === 0 && (
                    <p className="text-xs text-gray-500">No rules yet — every request will use the <code>default</code> group.</p>
                  )}

                  {(e.smart_rules_jsonb || []).map((rule, i) => {
                    const update = (patch: Partial<Rule>) => {
                      const v = [...(e.smart_rules_jsonb || [])];
                      v[i] = { ...rule, ...patch } as Rule;
                      setEditing({ ...e, smart_rules_jsonb: v });
                    };
                    const remove = () => setEditing({
                      ...e,
                      smart_rules_jsonb: (e.smart_rules_jsonb || []).filter((_, j) => j !== i),
                    });
                    const labelChip = (lbl: string) => (
                      <span className="rounded bg-emerald-100 px-2 py-0.5 font-mono text-xs text-emerald-700">{lbl}</span>
                    );
                    return (
                      <div key={i} className="mb-2 rounded border bg-white p-2">
                        <div className="flex flex-wrap items-center gap-2">
                          {rule.type === "preset" && (
                            <>
                              <span className="rounded bg-blue-100 px-2 py-0.5 text-xs text-blue-700">preset</span>
                              <span className="text-sm">{PRESET_BY_ID[(rule as any).id]?.title || (rule as any).id}</span>
                              <span className="text-xs text-gray-500">→</span>
                              {labelChip((rule as any).label || "")}
                            </>
                          )}
                          {rule.type === "tokens" && (
                            <>
                              <span className="rounded bg-gray-200 px-2 py-0.5 text-xs">tokens</span>
                              <span className="text-xs">tokens &gt;</span>
                              <input className="input w-20" type="number"
                                value={(rule as any).threshold}
                                onChange={(ev) => update({ threshold: +ev.target.value } as any)} />
                              <span className="text-xs">→</span>
                              {labelChip((rule as any).gt_label || "")}
                              <span className="text-xs">/ ≤ →</span>
                              {labelChip((rule as any).lte_label || "")}
                            </>
                          )}
                          {rule.type === "keyword" && (
                            <>
                              <span className="rounded bg-gray-200 px-2 py-0.5 text-xs">keyword</span>
                              <input className="input flex-1" placeholder="regex (case-insensitive) e.g. \\b(refund|chargeback)\\b"
                                value={(rule as any).pattern}
                                onChange={(ev) => update({ pattern: ev.target.value } as any)} />
                              <span className="text-xs">→</span>
                              {labelChip((rule as any).label || "")}
                            </>
                          )}
                          {rule.type === "code_block" && (
                            <>
                              <span className="rounded bg-gray-200 px-2 py-0.5 text-xs">code_block</span>
                              <span className="text-xs">contains ``` →</span>
                              {labelChip((rule as any).label || "")}
                            </>
                          )}
                          {rule.type === "geo" && (
                            <>
                              <span className="rounded bg-purple-100 px-2 py-0.5 text-xs text-purple-700">geo</span>
                              <span className="text-xs">client country in →</span>
                              {labelChip((rule as any).label || "")}
                              <LabelSelect
                                value={(rule as any).label || ""}
                                options={Array.from(new Set([...allLabels, (rule as any).label].filter(Boolean)))}
                                onChange={(v) => update({ label: v } as any)}
                                w="w-32"
                              />
                            </>
                          )}
                          <button className="btn-danger ml-auto" onClick={remove}>×</button>
                        </div>
                        {rule.type === "preset" && PRESET_BY_ID[(rule as any).id] && (
                          <p className="mt-1 text-xs text-gray-500">{PRESET_BY_ID[(rule as any).id].hint}</p>
                        )}
                        {rule.type === "geo" && (() => {
                          const selected: string[] = (rule as any).countries || [];
                          const selectedSet = new Set(selected.map((c) => c.toUpperCase()));
                          const removeCC = (cc: string) => update({
                            countries: selected.filter((c) => c.toUpperCase() !== cc.toUpperCase()),
                          } as any);
                          return (
                            <div className="mt-2 space-y-1">
                              <div className="flex flex-wrap items-center gap-1">
                                {selected.length === 0 && (
                                  <span className="text-xs text-gray-400">No countries — rule will never match.</span>
                                )}
                                {selected.map((cc) => (
                                  <span key={cc} className="flex items-center gap-1 rounded bg-purple-50 px-1.5 py-0.5 font-mono text-xs text-purple-700">
                                    {cc.toUpperCase()} · {COUNTRY_NAME[cc.toUpperCase()] || "?"}
                                    <button className="text-purple-500 hover:text-purple-900" onClick={() => removeCC(cc)}>×</button>
                                  </span>
                                ))}
                              </div>
                              <select
                                className="input text-xs"
                                value=""
                                onChange={(ev) => {
                                  const cc = ev.target.value;
                                  if (!cc || selectedSet.has(cc)) return;
                                  update({ countries: [...selected, cc] } as any);
                                  ev.target.value = "";
                                }}>
                                <option value="">+ Add country…</option>
                                {COUNTRIES.filter((c) => !selectedSet.has(c.code)).map((c) => (
                                  <option key={c.code} value={c.code}>{c.code} — {c.name}</option>
                                ))}
                              </select>
                              <p className="text-xs text-gray-500">
                                Country lookup uses the bundled DB-IP IP-to-Country Lite database (updated monthly). Override via <code>GEOIP_DB_PATH</code> if you want a custom build.
                              </p>
                            </div>
                          );
                        })()}
                      </div>
                    );
                  })}
                </div>
                )}

                {/* ---- Embedding classifier block ---- */}
                {e.smart_embedding_model_id != null && (
                <div>
                  <label className="label">Embedding model</label>
                  <select className="input w-full"
                    value={e.smart_embedding_model_id ?? ""}
                    onChange={(ev) => setEditing({
                      ...e,
                      smart_embedding_model_id: ev.target.value ? +ev.target.value : null,
                    })}>
                    {embeddingModels.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.display_name}</option>)}
                  </select>
                  {embeddingModels.length === 0 && (
                    <p className="mt-1 text-xs text-amber-700">No embedding models registered. Go to the Models page and add one with <code>kind=embedding</code> (e.g. <code>text-embedding-3-small</code>).</p>
                  )}
                  <p className="mt-1 text-xs text-gray-500">
                    The prompt is embedded and matched against the exemplars below by cosine similarity. Cheap (typically &lt;$0.0001/req) and cached per prompt for 24h.
                  </p>
                </div>
                )}

                {e.smart_embedding_model_id && (
                  <>
                    <div>
                      <div className="mb-1 flex items-center justify-between">
                        <label className="label !mb-0">Exemplars (3-10 per label is plenty)</label>
                        <button className="btn-outline text-xs"
                          onClick={() => setEditing({
                            ...e,
                            smart_exemplars_jsonb: [...exemplars, { label: "", text: "" }],
                          })}>+ Add exemplar</button>
                      </div>
                      {exemplars.length === 0 && (
                        <p className="text-xs text-gray-500">Add a few sample prompts per target label. The classifier picks the label whose exemplars are closest to the incoming prompt.</p>
                      )}
                      {exemplars.map((ex, i) => (
                        <div key={i} className="mb-2 flex items-start gap-2">
                          <input className="input w-32" placeholder="label"
                            value={ex.label}
                            onChange={(ev) => {
                              const arr = [...exemplars]; arr[i] = { ...ex, label: ev.target.value };
                              setEditing({ ...e, smart_exemplars_jsonb: arr });
                            }} />
                          <textarea className="input flex-1" rows={2}
                            placeholder="sample prompt that should route to this label"
                            value={ex.text}
                            onChange={(ev) => {
                              const arr = [...exemplars]; arr[i] = { ...ex, text: ev.target.value };
                              setEditing({ ...e, smart_exemplars_jsonb: arr });
                            }} />
                          <button className="btn-danger"
                            onClick={() => setEditing({
                              ...e,
                              smart_exemplars_jsonb: exemplars.filter((_, j) => j !== i),
                            })}>×</button>
                        </div>
                      ))}
                    </div>

                    <div>
                      <label className="label">Score threshold ({e.smart_score_threshold ?? 55}%)</label>
                      <input type="range" min={0} max={100} step={1}
                        className="w-full"
                        value={e.smart_score_threshold ?? 55}
                        onChange={(ev) => setEditing({ ...e, smart_score_threshold: +ev.target.value })} />
                      <p className="mt-1 text-xs text-gray-500">
                        Cosine similarity cutoff. Below this, use the <code>default</code> group. 55% is a sensible starting point — raise to be stricter, lower to route more aggressively.
                      </p>
                    </div>
                  </>
                )}

                <p className="text-xs text-gray-500">
                  Active labels: {allLabels.map((l) => (
                    <span key={l} className="ml-1 rounded bg-gray-200 px-1.5 py-0.5 font-mono">{l}</span>
                  ))}
                  <span className="ml-2 italic">(targets without a label belong to <code>default</code>)</span>
                </p>
              </div>
            )}

            {e.strategy === "smart" && renderSmartTargets()}

            {e.strategy === "smart" && renderFallbackControl()}

            <div>
              <label className="label">Scope (visibility)</label>
              <select className="input w-full" value={e.scope}
                onChange={(ev) => setEditing({ ...e, scope: ev.target.value as R["scope"] })}>
                <option value="public">public — listed in /v1/models and callable by users</option>
                <option value="private">private — hidden &amp; not user-callable</option>
              </select>
            </div>

            <label className="flex items-center gap-2">
              <input type="checkbox" checked={e.enabled}
                onChange={(ev) => setEditing({ ...e, enabled: ev.target.checked })} />
              Enabled
            </label>
        </Modal>
        );
      })()}
    </div>
  );
}
