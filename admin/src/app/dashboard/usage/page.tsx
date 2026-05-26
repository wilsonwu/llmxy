"use client";
import useSWR from "swr";
import { useState } from "react";
import { fetcher } from "@/lib/api";

type Log = {
  id: number;
  user_id: number;
  api_key_id?: number | null;
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

type Tx = {
  id: number;
  user_id: number;
  type: string;
  amount_cents: number;
  balance_after: number;
  ref_id: string | null;
  note: string | null;
  created_at: string;
};

const PAGE_SIZE = 30;
type Tab = "logs" | "tx";

export default function AdminUsagePage() {
  const [tab, setTab] = useState<Tab>("logs");

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <h1 className="text-2xl font-bold">Usage &amp; billing</h1>
        <div className="inline-flex rounded border bg-white text-sm">
          <button
            className={`px-4 py-1.5 ${tab === "logs" ? "bg-brand-600 text-white" : ""}`}
            onClick={() => setTab("logs")}>Usage logs</button>
          <button
            className={`px-4 py-1.5 ${tab === "tx" ? "bg-brand-600 text-white" : ""}`}
            onClick={() => setTab("tx")}>Balance transactions</button>
        </div>
      </div>

      {tab === "logs" ? <LogsPanel /> : <TxPanel />}
    </div>
  );
}

// ---------------------------------------------------------------------------
function LogsPanel() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    user_id: "", api_key_id: "", model_id: "",
    user_facing_model: "", upstream_model: "", status: "",
    kind: "", resolved_label: "", start: "", end: "",
  });
  const [applied, setApplied] = useState(filters);

  const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  for (const [k, v] of Object.entries(applied)) if (v) qs.set(k, v);

  const { data } = useSWR<{ items: Log[]; total: number; page: number; page_size: number }>(
    `/api/v1/admin/usage/logs?${qs.toString()}`, fetcher
  );
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const apply = () => { setPage(1); setApplied(filters); };
  const reset = () => {
    const blank = { user_id: "", api_key_id: "", model_id: "", user_facing_model: "", upstream_model: "", status: "", kind: "", resolved_label: "", start: "", end: "" };
    setFilters(blank); setApplied(blank); setPage(1);
  };

  return (
    <>
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-4">
        <input className="input" placeholder="user_id" value={filters.user_id} onChange={(e) => setFilters({ ...filters, user_id: e.target.value })} />
        <input className="input" placeholder="api_key_id" value={filters.api_key_id} onChange={(e) => setFilters({ ...filters, api_key_id: e.target.value })} />
        <input className="input" placeholder="model_id" value={filters.model_id} onChange={(e) => setFilters({ ...filters, model_id: e.target.value })} />
        <input className="input" placeholder="status (ok / error_*)" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} />
        <input className="input" placeholder="user_facing_model" value={filters.user_facing_model} onChange={(e) => setFilters({ ...filters, user_facing_model: e.target.value })} />
        <input className="input" placeholder="upstream_model" value={filters.upstream_model} onChange={(e) => setFilters({ ...filters, upstream_model: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.start} onChange={(e) => setFilters({ ...filters, start: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.end} onChange={(e) => setFilters({ ...filters, end: e.target.value })} />
        <select className="input" value={filters.kind} onChange={(e) => setFilters({ ...filters, kind: e.target.value })}>
          <option value="">kind (all)</option>
          <option value="relay">relay</option>
          <option value="classifier">classifier</option>
        </select>
        <input className="input" placeholder="resolved_label" value={filters.resolved_label} onChange={(e) => setFilters({ ...filters, resolved_label: e.target.value })} />
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
                <td>{l.api_key_id ?? "-"}</td>
                <td>{l.user_facing_model || "-"}</td>
                <td className="font-mono text-xs">{l.upstream_model || "-"}</td>
                <td>
                  {l.kind === "classifier"
                    ? <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">classifier</span>
                    : <span className="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-800">relay</span>}
                </td>
                <td>
                  {l.resolved_label
                    ? <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">{l.resolved_label}</span>
                    : <span className="text-gray-400">-</span>}
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

      <Pager page={page} pageCount={pageCount} total={total} setPage={setPage} />
    </>
  );
}

// ---------------------------------------------------------------------------
const TX_TYPES = ["", "topup", "consume", "refund", "grant"] as const;

function TxPanel() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ user_id: "", type: "", ref_id: "", start: "", end: "" });
  const [applied, setApplied] = useState(filters);

  const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  for (const [k, v] of Object.entries(applied)) if (v) qs.set(k, v);

  const { data } = useSWR<{ items: Tx[]; total: number; page: number; page_size: number }>(
    `/api/v1/admin/usage/balance-tx?${qs.toString()}`, fetcher
  );
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const apply = () => { setPage(1); setApplied(filters); };
  const reset = () => {
    const blank = { user_id: "", type: "", ref_id: "", start: "", end: "" };
    setFilters(blank); setApplied(blank); setPage(1);
  };

  const typeBadge = (t: string) => {
    const color =
      t === "topup" ? "bg-green-100 text-green-800"
      : t === "consume" ? "bg-red-100 text-red-800"
      : t === "refund" ? "bg-blue-100 text-blue-800"
      : t === "grant" ? "bg-purple-100 text-purple-800"
      : "bg-gray-100 text-gray-700";
    return <span className={`rounded px-1.5 py-0.5 text-xs ${color}`}>{t}</span>;
  };

  return (
    <>
      <div className="card grid grid-cols-2 gap-3 md:grid-cols-5">
        <input className="input" placeholder="user_id" value={filters.user_id} onChange={(e) => setFilters({ ...filters, user_id: e.target.value })} />
        <select className="input" value={filters.type} onChange={(e) => setFilters({ ...filters, type: e.target.value })}>
          {TX_TYPES.map((t) => <option key={t} value={t}>{t || "type (all)"}</option>)}
        </select>
        <input className="input" placeholder="ref_id (request_id / order_id)" value={filters.ref_id} onChange={(e) => setFilters({ ...filters, ref_id: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.start} onChange={(e) => setFilters({ ...filters, start: e.target.value })} />
        <input className="input" type="datetime-local" value={filters.end} onChange={(e) => setFilters({ ...filters, end: e.target.value })} />
        <div className="col-span-2 flex gap-2 md:col-span-5">
          <button className="btn-primary" onClick={apply}>Apply</button>
          <button className="btn-outline" onClick={reset}>Reset</button>
        </div>
      </div>

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Time</th><th>User</th><th>Type</th>
              <th>Amount</th><th>Balance after</th>
              <th>Ref</th><th>Note</th>
            </tr>
          </thead>
          <tbody>
            {data?.items?.map((t) => (
              <tr key={t.id}>
                <td>{new Date(t.created_at).toLocaleString()}</td>
                <td>{t.user_id}</td>
                <td>{typeBadge(t.type)}</td>
                <td className={t.amount_cents >= 0 ? "text-green-600" : "text-red-600"}>
                  {t.amount_cents >= 0 ? "+" : "-"}${Math.abs(t.amount_cents / 100).toFixed(4)}
                </td>
                <td>${(t.balance_after / 100).toFixed(2)}</td>
                <td className="font-mono text-xs">{t.ref_id || "-"}</td>
                <td className="text-xs text-gray-600">{t.note || "-"}</td>
              </tr>
            ))}
            {!data?.items?.length && (
              <tr><td colSpan={7} className="text-center text-gray-500">No records</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <Pager page={page} pageCount={pageCount} total={total} setPage={setPage} />
    </>
  );
}

// ---------------------------------------------------------------------------
function Pager({ page, pageCount, total, setPage }: { page: number; pageCount: number; total: number; setPage: (f: (p: number) => number) => void }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <div className="text-gray-500">{total} total · page {page} / {pageCount}</div>
      <div className="space-x-2">
        <button className="btn-outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
        <button className="btn-outline" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>
    </div>
  );
}
