"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Log = {
  id: number; user_facing_model: string; upstream_model: string;
  prompt_tokens: number; completion_tokens: number;
  cost_cents: number; latency_ms: number; status: string; created_at: string;
};

export default function UsagePage() {
  const { data } = useSWR<{ items: Log[]; total: number }>("/api/v1/usage/logs?page=1&page_size=50", fetcher);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">用量</h1>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr><th>时间</th><th>模型</th><th>上游</th><th>prompt</th><th>completion</th><th>费用</th><th>延迟</th><th>状态</th></tr>
          </thead>
          <tbody>
            {data?.items?.map((l) => (
              <tr key={l.id}>
                <td>{new Date(l.created_at).toLocaleString()}</td>
                <td>{l.user_facing_model}</td>
                <td>{l.upstream_model}</td>
                <td>{l.prompt_tokens}</td>
                <td>{l.completion_tokens}</td>
                <td>¥{(l.cost_cents / 100).toFixed(4)}</td>
                <td>{l.latency_ms}ms</td>
                <td>{l.status}</td>
              </tr>
            ))}
            {!data?.items?.length && <tr><td colSpan={8} className="text-center text-gray-500">暂无记录</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
