"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type Target = { model_id: number; weight: number; fallback_order: number; label?: string | null };
type Rule =
  | { type: "preset"; id: string; label: string }
  | { type: "tokens"; threshold: number; gt_label: string; lte_label: string }
  | { type: "keyword"; pattern: string; label: string }
  | { type: "code_block"; label: string };
type R = {
  id?: number;
  user_facing_model: string;
  strategy: "weighted" | "smart" | "fallback";
  targets_jsonb: Target[];
  smart_classifier_model_id?: number | null;
  smart_rules_jsonb?: Rule[];
  smart_default_label?: string | null;
  smart_classifier_hint?: string | null;
  scope: "public" | "private";
  enabled: boolean;
};
type M = { id: number; code: string; display_name: string };

const empty: R = {
  user_facing_model: "",
  strategy: "weighted",
  targets_jsonb: [],
  smart_classifier_model_id: null,
  smart_rules_jsonb: [],
  smart_default_label: null,
  smart_classifier_hint: null,
  scope: "public",
  enabled: true,
};

const STRATEGY_DESC: Record<R["strategy"], { title: string; body: string }> = {
  weighted: {
    title: "weighted — 加权随机分流",
    body: "按 weight 加权随机抽中主目标；其余目标按权重随机排成兜底链。适合多渠道分摊成本、A/B 灰度、多 Key 限流均衡。配置：只需填 weight。",
  },
  fallback: {
    title: "fallback — 优先级 + 顺序兜底",
    body: "按 order 升序排，第一个为主，其余按顺序兜底。适合“主用便宜渠道，挂了切贵的”这种有明确优先级的场景。配置：只需填 order。",
  },
  smart: {
    title: "smart — 按 prompt 内容智能选择",
    body:
      "二选一：规则匹配（内置预设，零代码）或 AI 分类器（让模型判断走哪个 label）。命中后选对应 target，其余作为兜底；都未命中则回退到 default label，再无则降级为 weighted。",
  },
};

// Built-in rule presets — admin picks "intent", server has the regex.
// Each preset also pre-assigns its `label` so the admin never has to type one.
const PRESETS: { id: string; title: string; hint: string; label: string }[] = [
  { id: "code_block", title: "Contains code block (```)", hint: "Prompt has ``` fences — programming/code review tasks", label: "code" },
  { id: "long_prompt", title: "Long prompt (~>800 tokens)", hint: "Long context — usually needs stronger model", label: "long" },
  { id: "short_prompt", title: "Short prompt (~≤80 tokens)", hint: "Tiny question — cheap model fine", label: "short" },
  { id: "translate", title: "Translation request", hint: "Mentions translate / 翻译 / translation", label: "translate" },
  { id: "math", title: "Math / calculation", hint: "Mentions solve / equation / 求解 / 证明 / LaTeX markers", label: "math" },
  { id: "reasoning", title: "Reasoning / step-by-step", hint: "Mentions step-by-step / chain of thought / 推理", label: "reasoning" },
  { id: "summarize", title: "Summarization", hint: "Mentions summarize / tl;dr / 总结 / 摘要", label: "summarize" },
  { id: "creative", title: "Creative writing", hint: "Mentions story / poem / 故事 / 小说", label: "creative" },
  { id: "chinese", title: "Chinese (CJK ≥30%)", hint: "Mostly Chinese characters", label: "chinese" },
  { id: "english", title: "English-only", hint: "Almost no CJK characters", label: "english" },
];

const PRESET_BY_ID = Object.fromEntries(PRESETS.map((p) => [p.id, p]));

// Always-present fallback label — auto-assigned as smart_default_label on save.
// Admin assigns it to one target as the "catch-all".
const DEFAULT_LABEL = "default";

export default function RoutesPage() {
  const { data, mutate } = useSWR<R[]>("/api/v1/admin/routes", fetcher);
  const { data: models } = useSWR<M[]>("/api/v1/admin/models", fetcher);
  const [editing, setEditing] = useState<R | null>(null);
  const [q, setQ] = useState("");
  const filtered = (data || []).filter(r => !q || r.user_facing_model.toLowerCase().includes(q.toLowerCase()));

  async function save(r: R) {
    const payload = { ...r };
    if (r.strategy !== "smart") {
      payload.smart_classifier_model_id = null;
      payload.smart_rules_jsonb = [];
      payload.smart_default_label = null;
      payload.smart_classifier_hint = null;
    } else {
      // Fallback label is fixed — no UI for it.
      payload.smart_default_label = DEFAULT_LABEL;
    }
    if (r.id) await api(`/api/v1/admin/routes/${r.id}`, { method: "PUT", body: JSON.stringify(payload) });
    else await api(`/api/v1/admin/routes`, { method: "POST", body: JSON.stringify(payload) });
    setEditing(null);
    mutate();
  }
  async function del(id: number) {
    if (!confirm("Delete this route?")) return;
    await api(`/api/v1/admin/routes/${id}`, { method: "DELETE" });
    mutate();
  }
  const modelLabel = (id: number) => models?.find((m) => m.id === id)?.code || `#${id}`;

  const renderTargetSummary = (r: R) =>
    r.targets_jsonb.map((t, i) => {
      const extras: string[] = [];
      if (r.strategy === "weighted") extras.push(`w${t.weight}`);
      if (r.strategy === "fallback") extras.push(`o${t.fallback_order}`);
      if (r.strategy === "smart") extras.push(t.label ? `[${t.label}]` : "[no-label]");
      return (
        <span key={i} className="mr-2">
          {modelLabel(t.model_id)}({extras.join("/")})
        </span>
      );
    });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Smart routing</h1>
        <div className="flex items-center gap-2">
          <input className="input" placeholder="Search public model name" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-primary" onClick={() => setEditing({ ...empty, targets_jsonb: [] })}>New</button>
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

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr><th>ID</th><th>Public model</th><th>Strategy</th><th>Scope</th><th>targets</th><th>Enabled</th><th></th></tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.user_facing_model}</td>
                <td>
                  {r.strategy}
                  {r.targets_jsonb.length <= 1 && (
                    <span title="Only one target — strategy has no effect" className="ml-1 text-xs text-amber-600">(single target)</span>
                  )}
                </td>
                <td>
                  <span className={`rounded px-2 py-0.5 text-xs ${r.scope === "private" ? "bg-amber-100 text-amber-700" : "bg-green-100 text-green-700"}`}>
                    {r.scope}
                  </span>
                </td>
                <td className="text-xs">{renderTargetSummary(r)}</td>
                <td>{r.enabled ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({
                    ...r,
                    targets_jsonb: [...r.targets_jsonb],
                    smart_rules_jsonb: [...(r.smart_rules_jsonb || [])],
                  })}>Edit</button>
                  <button className="btn-danger" onClick={() => del(r.id!)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (() => {
        const e = editing;  // narrow non-null for closures
        const usingClassifier = !!e.smart_classifier_model_id;
        const smartMode: "rules" | "classifier" = usingClassifier ? "classifier" : "rules";

        // Labels referenced by rules (rules-mode source of truth) + the always-on default label.
        const ruleLabels: string[] = Array.from(new Set([
          ...(e.smart_rules_jsonb || []).flatMap((r: any) => {
            if (r.type === "tokens") return [r.gt_label, r.lte_label];
            return [r.label];
          }).filter((s: any): s is string => typeof s === "string" && s.trim().length > 0),
          DEFAULT_LABEL,
        ]));
        // Classifier mode label pool: PRESET-defined labels + default.
        const classifierLabels: string[] = Array.from(new Set([
          ...PRESETS.map((p) => p.label),
          "cheap", "strong", "fast",
          DEFAULT_LABEL,
        ]));

        const setMode = (m: "rules" | "classifier") => {
          if (m === "classifier") {
            setEditing({ ...e, smart_rules_jsonb: [],
              smart_classifier_model_id: e.smart_classifier_model_id || models?.[0]?.id || null });
          } else {
            setEditing({ ...e, smart_classifier_model_id: null, smart_classifier_hint: null });
          }
        };

        // Auto-name a new keyword rule slot — kw_1, kw_2, … (skip taken).
        const nextKeywordLabel = (): string => {
          const taken = new Set((e.smart_rules_jsonb || []).map((r: any) => r.label).filter(Boolean));
          for (let n = 1; n < 999; n++) {
            const cand = `kw_${n}`;
            if (!taken.has(cand)) return cand;
          }
          return "kw_x";
        };

        const addPreset = (pid: string) => {
          if (!pid) return;
          let rule: Rule;
          if (pid === "__custom_keyword") {
            rule = { type: "keyword", pattern: "", label: nextKeywordLabel() };
          } else {
            const meta = PRESET_BY_ID[pid];
            if (!meta) return;
            rule = { type: "preset", id: pid, label: meta.label };
          }
          setEditing({ ...e, smart_rules_jsonb: [...(e.smart_rules_jsonb || []), rule] });
        };

        // Strict dropdown — used for target.label everywhere (no typing allowed).
        const LabelSelect = ({ value, onChange, options, w = "w-40", emptyText = "— pick label —" }:
          { value: string; onChange: (v: string) => void; options: string[]; w?: string; emptyText?: string }) => (
          <select className={`input ${w}`} value={value} onChange={(ev) => onChange(ev.target.value)}>
            <option value="">{emptyText}</option>
            {options.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        );

        const targetLabelOptions = smartMode === "rules" ? ruleLabels : classifierLabels;

        const renderTargets = () => (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="label !mb-0">Targets</label>
              <button className="btn-outline text-xs" onClick={() => setEditing({
                ...e,
                targets_jsonb: [...e.targets_jsonb, {
                  model_id: models?.[0]?.id || 0,
                  weight: 1,
                  fallback_order: e.targets_jsonb.length,
                  label: "",
                }],
              })}>+ Add</button>
            </div>
            {e.strategy === "smart" && (
              <p className="mb-2 text-xs text-gray-500">
                {smartMode === "rules"
                  ? <>Pick a label for each target — labels come from the rules above. The <code>default</code> label catches anything that doesn&apos;t match a rule.</>
                  : <>Pick a label for each target — the AI classifier will choose among them per request. <code>default</code> is the fallback if it fails.</>}
              </p>
            )}
            {e.targets_jsonb.map((t, i) => (
              <div key={i} className="mb-2 flex items-center gap-2">
                <select className="input flex-1" value={t.model_id} onChange={(ev) => {
                  const v = [...e.targets_jsonb]; v[i] = { ...t, model_id: +ev.target.value };
                  setEditing({ ...e, targets_jsonb: v });
                }}>
                  {models?.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.display_name}</option>)}
                </select>
                {e.strategy === "weighted" && (
                  <input className="input w-20" type="number" placeholder="weight" value={t.weight}
                    onChange={(ev) => {
                      const v = [...e.targets_jsonb]; v[i] = { ...t, weight: +ev.target.value };
                      setEditing({ ...e, targets_jsonb: v });
                    }} />
                )}
                {e.strategy === "fallback" && (
                  <input className="input w-20" type="number" placeholder="order" value={t.fallback_order}
                    onChange={(ev) => {
                      const v = [...e.targets_jsonb]; v[i] = { ...t, fallback_order: +ev.target.value };
                      setEditing({ ...e, targets_jsonb: v });
                    }} />
                )}
                {e.strategy === "smart" && (
                  <LabelSelect value={t.label || ""} options={targetLabelOptions}
                    onChange={(v) => {
                      const arr = [...e.targets_jsonb]; arr[i] = { ...t, label: v };
                      setEditing({ ...e, targets_jsonb: arr });
                    }} />
                )}
                <button className="btn-danger" onClick={() => {
                  const v = e.targets_jsonb.filter((_, j) => j !== i);
                  setEditing({ ...e, targets_jsonb: v });
                }}>×</button>
              </div>
            ))}
          </div>
        );

        return (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card max-h-[90vh] w-[760px] space-y-3 overflow-y-auto">
            <h2 className="text-lg font-semibold">{e.id ? "Edit" : "New"} route</h2>

            <div>
              <label className="label">Public model name</label>
              <input className="input w-full" value={e.user_facing_model}
                onChange={(ev) => setEditing({ ...e, user_facing_model: ev.target.value })} />
            </div>

            <div>
              <label className="label">Scope (visibility)</label>
              <select className="input w-full" value={e.scope}
                onChange={(ev) => setEditing({ ...e, scope: ev.target.value as R["scope"] })}>
                <option value="public">public — listed in /v1/models and callable by users</option>
                <option value="private">private — hidden & not user-callable (use as smart classifier target etc.)</option>
              </select>
            </div>

            <div>
              <label className="label">Strategy</label>
              <select className="input w-full" value={e.strategy}
                onChange={(ev) => setEditing({ ...e, strategy: ev.target.value as R["strategy"] })}>
                <option value="weighted">weighted</option>
                <option value="fallback">fallback</option>
                <option value="smart">smart</option>
              </select>
              <p className="mt-1 text-xs text-gray-500">{STRATEGY_DESC[e.strategy].body}</p>
              {e.targets_jsonb.length <= 1 && (
                <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
                  Only one target — strategy is effectively a no-op (no weighting / fallback / classification to do).
                  Add more targets below to make {e.strategy} meaningful.
                </p>
              )}
            </div>

            {/* For non-smart strategies, targets render here (above no smart config). */}
            {e.strategy !== "smart" && renderTargets()}

            {e.strategy === "smart" && (
              <div className="space-y-3 rounded border bg-gray-50 p-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold">Smart routing decision</div>
                  <div className="inline-flex rounded border bg-white text-xs">
                    <button
                      className={`px-3 py-1 ${smartMode === "rules" ? "bg-blue-600 text-white" : ""}`}
                      onClick={() => setMode("rules")}>Rule-based</button>
                    <button
                      className={`px-3 py-1 ${smartMode === "classifier" ? "bg-blue-600 text-white" : ""}`}
                      onClick={() => setMode("classifier")}>AI classifier</button>
                  </div>
                </div>

                {smartMode === "rules" ? (
                  <>
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
                        </select>
                      </div>

                      {(e.smart_rules_jsonb || []).length === 0 && (
                        <p className="text-xs text-gray-500">No rules yet — pick a preset above. Each rule emits a label that you assign to a target below. Anything unmatched falls back to <code>default</code>.</p>
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
                                  <input className="input flex-1" placeholder="regex (case-insensitive) e.g. \\b(refund|退款)\\b"
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
                              <button className="btn-danger ml-auto" onClick={remove}>×</button>
                            </div>
                            {rule.type === "preset" && PRESET_BY_ID[(rule as any).id] && (
                              <p className="mt-1 text-xs text-gray-500">{PRESET_BY_ID[(rule as any).id].hint}</p>
                            )}
                          </div>
                        );
                      })}

                      <p className="mt-1 text-xs text-gray-500">
                        Active labels: {ruleLabels.map((l) => (
                          <span key={l} className="ml-1 rounded bg-gray-200 px-1.5 py-0.5 font-mono">{l}</span>
                        ))}
                        <span className="ml-2 italic">(<code>default</code> is the always-on fallback)</span>
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <label className="label">Classifier model</label>
                      <select className="input w-full"
                        value={e.smart_classifier_model_id ?? ""}
                        onChange={(ev) => setEditing({
                          ...e,
                          smart_classifier_model_id: ev.target.value ? +ev.target.value : null,
                        })}>
                        <option value="">— select a model —</option>
                        {models?.map((m) => <option key={m.id} value={m.id}>{m.code} — {m.display_name}</option>)}
                      </select>
                      <p className="mt-1 text-xs text-gray-500">
                        On each request, this model is asked to pick one of your target labels. Result is cached 1h per prompt.
                        Choose a small / cheap model for cost reasons.
                      </p>
                    </div>

                    <div>
                      <label className="label">Routing instruction (optional)</label>
                      <textarea className="input w-full" rows={2}
                        placeholder="e.g. Prefer 'cheap' unless the request needs deep reasoning, code generation, or long outputs — then pick 'strong'."
                        value={e.smart_classifier_hint || ""}
                        onChange={(ev) => setEditing({ ...e, smart_classifier_hint: ev.target.value || null })} />
                      <p className="mt-1 text-xs text-gray-500">
                        Appended to the classifier's system prompt — describe in plain English how you want it to choose.
                        If it fails or returns an unknown label, the route falls back to the target labeled <code>default</code>.
                      </p>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* For smart strategy, targets render AFTER smart config — admin defines
                labels (via rules) first, then assigns them to targets here. */}
            {e.strategy === "smart" && renderTargets()}

            <label className="flex items-center gap-2">
              <input type="checkbox" checked={e.enabled}
                onChange={(ev) => setEditing({ ...e, enabled: ev.target.checked })} />
              Enabled
            </label>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={() => save(e)}>Save</button>
            </div>
          </div>
        </div>
        );
      })()}
    </div>
  );
}
