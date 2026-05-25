"use client";
import useSWR from "swr";
import Link from "next/link";
import { useParams } from "next/navigation";
import { fetcher } from "@/lib/api";

type Sub = {
  id: number;
  plan_id: number;
  plan_code?: string;
  plan_name?: string;
  start_at: string;
  end_at: string;
  status: string;
  remaining_cents: number;
};

type Detail = {
  user: { id: number; email: string; role: string; balance_cents: number; status: string; created_at: string };
  subscriptions: Sub[];
  spent_total_cents: number;
  spent_30d_cents: number;
  requests_total: number;
  requests_30d: number;
};

export default function UserDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const { data } = useSWR<Detail>(id ? `/api/v1/admin/users/${id}/detail` : null, fetcher);
  if (!data) return <p className="text-gray-500">Loading…</p>;
  const now = Date.now();
  const active = data.subscriptions.filter((s) => s.status === "active" && new Date(s.end_at).getTime() > now && s.remaining_cents > 0);
  const others = data.subscriptions.filter((s) => !active.includes(s));
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/dashboard/users" className="text-sm text-gray-500 hover:underline">← Back</Link>
        <h1 className="text-2xl font-bold">{data.user.email}</h1>
        <span className="text-sm text-gray-500">#{data.user.id} · {data.user.status}</span>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div className="card"><p className="text-xs text-gray-500">Wallet balance</p><p className="mt-1 text-2xl font-bold">${(data.user.balance_cents/100).toFixed(2)}</p></div>
        <div className="card"><p className="text-xs text-gray-500">Spent (30d)</p><p className="mt-1 text-2xl font-bold">${(data.spent_30d_cents/100).toFixed(2)}</p></div>
        <div className="card"><p className="text-xs text-gray-500">Spent (total)</p><p className="mt-1 text-2xl font-bold">${(data.spent_total_cents/100).toFixed(2)}</p></div>
        <div className="card"><p className="text-xs text-gray-500">Requests (30d / total)</p><p className="mt-1 text-2xl font-bold">{data.requests_30d} / {data.requests_total}</p></div>
      </div>

      <div className="card overflow-x-auto">
        <h2 className="mb-3 text-lg font-semibold">Active subscriptions</h2>
        {active.length === 0 ? (
          <p className="text-sm text-gray-500">No active subscription. Requests draw from wallet balance.</p>
        ) : (
          <table className="table">
            <thead><tr><th>Order</th><th>Plan</th><th>Remaining</th><th>Started</th><th>Expires</th></tr></thead>
            <tbody>
              {active.slice().sort((a, b) => new Date(a.end_at).getTime() - new Date(b.end_at).getTime()).map((s, i) => (
                <tr key={s.id}>
                  <td>{i + 1}</td>
                  <td>{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                  <td>${(s.remaining_cents/100).toFixed(2)}</td>
                  <td>{new Date(s.start_at).toLocaleDateString()}</td>
                  <td>{new Date(s.end_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {others.length > 0 && (
        <div className="card overflow-x-auto">
          <h2 className="mb-3 text-lg font-semibold">Past / exhausted</h2>
          <table className="table">
            <thead><tr><th>Plan</th><th>Remaining</th><th>Status</th><th>Started</th><th>Ended</th></tr></thead>
            <tbody>
              {others.map((s) => (
                <tr key={s.id} className="text-gray-500">
                  <td>{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                  <td>${(s.remaining_cents/100).toFixed(2)}</td>
                  <td>{s.status}</td>
                  <td>{new Date(s.start_at).toLocaleDateString()}</td>
                  <td>{new Date(s.end_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
