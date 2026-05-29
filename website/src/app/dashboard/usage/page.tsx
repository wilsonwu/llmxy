"use client";
import useSWR from "swr";
import { useState } from "react";
import { fetcher } from "@/lib/api";
import { Badge, EmptyState, TableSkeleton } from "@/components/ui";

type Log = {
  id: number; user_facing_model: string; upstream_model: string;
  prompt_tokens: number; completion_tokens: number;
  cost_cents: number; latency_ms: number; status: string; created_at: string;
  kind?: string; resolved_label?: string | null;
};

const PAGE_SIZE = 20;

export default function UsagePage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useSWR<{ items: Log[]; total: number; page: number; page_size: number }>(
    `/api/v1/usage/logs?page=${page}&page_size=${PAGE_SIZE}`,
    fetcher
  );
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Usage</h1>
      <div className="card overflow-x-auto p-0">
        <table className="table">
          <thead>
            <tr><th>Time</th><th>Model</th><th>Upstream</th><th>Kind</th><th>Label</th><th>prompt</th><th>completion</th><th>Cost</th><th>Latency</th><th>Status</th></tr>
          </thead>
          <tbody>
            {isLoading && <TableSkeleton cols={10} />}
            {!isLoading && data?.items?.map((l) => (
              <tr key={l.id}>
                <td>{new Date(l.created_at).toLocaleString()}</td>
                <td>{l.user_facing_model}</td>
                <td>{l.upstream_model}</td>
                <td>
                  {l.kind === "classifier"
                    ? <Badge tone="warning">classifier</Badge>
                    : <Badge tone="info">relay</Badge>}
                </td>
                <td>{l.resolved_label
                  ? <Badge tone="success">{l.resolved_label}</Badge>
                  : <span className="text-gray-400">-</span>}</td>
                <td>{l.prompt_tokens}</td>
                <td>{l.completion_tokens}</td>
                <td>${(l.cost_cents / 100).toFixed(4)}</td>
                <td>{l.latency_ms}ms</td>
                <td>{l.status}</td>
              </tr>
            ))}
            {!isLoading && !data?.items?.length && (
              <tr><td colSpan={10}><EmptyState title="No usage yet" hint="Requests against /v1/* will appear here as they happen." /></td></tr>
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
