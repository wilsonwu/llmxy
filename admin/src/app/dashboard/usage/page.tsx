"use client";
import useSWR from "swr";
import { useState } from "react";
import { fetcher } from "@/lib/api";

type Log = {
  id: number;
  user_id: number;
  model_id: number | null;
  user_facing_model: string | null;
  upstream_model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_cents: number;
  latency_ms: number;
  status: string;
  kind?: string;
  resolved_label?: string | null;
  created_at: string;
};

const PAGE_SIZE = 30;

export default function AdminUsagePage() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    user_id: "",
    api_key_id: "",
    model_id: "",
    user_facing_model: "",
    upstream_model: "",
    status: "",
    kind: "",
    resolved_label: "",
    start: "",
    end: "",
  });
  const [applied, setApplied] = useState(filters);

  const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  for (const [k, v] of Object.entries(applied)) if (v) qs.set(k, v);

  const { data } = useSWR<{ items: Log[]; total: number; page: number; page_size: number }>(
    `/api/v1/admin/usage/logs?${qs.toString()}`,
    fetcher
  );
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function apply() {
    setPage(1);
    setApplied(filters);
  }
  function reset() {
    const blank = { user_id: "", api_key_id: "", model_id: "", user_facing_model: "", upstream_model: "", status: "", kind: "", resolved_label: "", start: "", end: "" };
    setFilters(blank);
    setApplied(blank);
    setPage(1);
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Usage logs</h1>
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-4">
        <input className="input" placeholder="user_id" value={filters.user_id}
          onChange={(e) => setFilters({ ...filters, user_id: e.target.value })} />
        <input className="input" placeholder="api_key_id" value={filters.api_key_id}
          onChange={(e) => setFilters({ ...filters, api_key_id: e.target.value })} />
        <input className="input" placeholder="model_id" value={filters.model_id}
          onChange={(e) => setFilters({ ...filters, model_id: e.target.value })} />
        <input className="input" placeholder="status (ok / error_*)" value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })} />
        <input className="input" placeholder="user_facing_model" value={filters.user_facing_model}
          onChange={(e) => setFilters({ ...filters, user_facing_model: e.target.value })} />
        <input className="input" placeholder="upstream_model" value={filters.upstream_model}
          onChange={(e) => setFilters({ ...filters, upstream_model: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.start}
          onChange={(e) => setFilters({ ...filters, start: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.end}
          onChange={(e) => setFilters({ ...filters, end: e.target.value })} />
        <select className="input" value={filters.kind}
          onChange={(e) => setFilters({ ...filters, kind: e.target.value })}>
          <option value="">kind (all)</option>
          <option value="relay">relay</option>
          <option value="classifier">classifier</option>
        </select>
        <input className="input" placeholder="resolved_label" value={filters.resolved_label}
          onChange={(e) => setFilters({ ...filters, resolved_label: e.target.value })} />
        <div className="col-span-2 flex gap-2 md:col-span-4">
          <button className="btn-primary" onClick={apply}>Apply</button>
          <button className="btn-outline" onClick={reset}>Reset</button>
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Time</th><th>User</th><th>Key</th>
              <th>User-facing model</th><th>Upstream model</th>
              <th>Kind</th><th>Label</th>
              <th>prompt</th><th>completion</th>
              <th>Cost</th><th>Latency</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {data?.items?.map((l) => (
              <tr key={l.id}>
                <td>{new Date(l.created_at).toLocaleString()}</td>
                <td>{l.user_id}</td>
                <td>{(l as any).api_key_id ?? "-"}</td>
                <td>{l.user_facing_model || "-"}</td>
                <td className="font-mono text-xs">{l.upstream_model || "-"}</td>
                <td>
                  {l.kind === "classifier" ? (
                    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">classifier</span>
                  ) : (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-800">relay</span>
                  )}
                </td>
                <td>
                  {l.resolved_label ? (
                    <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">{l.resolved_label}</span>
                  ) : (
                    <span className="text-gray-400">-</span>
                  )}
                </td>
                <td>{l.prompt_tokens}</td>
                <td>{l.completion_tokens}</td>
                <td>${(l.cost_cents / 100).toFixed(4)}</td>
                <td>{l.latency_ms}ms</td>
                <td>{l.status}</td>
              </tr>
            ))}
            {!data?.items?.length && (
              <tr><td colSpan={12} className="text-center text-gray-500">No records</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm">
        <div className="text-gray-500">{total} total · page {page} / {pageCount}</div>
        <div className="space-x-2">
          <button className="btn-outline" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
          <button className="btn-outline" disabled={page >= pageCount} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      </div>
    </div>
  );
}
