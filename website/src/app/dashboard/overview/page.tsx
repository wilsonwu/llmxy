"use client";
import useSWR from "swr";
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

export default function Overview() {
  const { data: me } = useSWR<{ email: string; balance_cents: number; role: string }>("/api/v1/auth/me", fetcher);
  const { data: subs } = useSWR<Sub[]>("/api/v1/usage/subscriptions", fetcher);
  const now = Date.now();
  const active = (subs || []).filter((s) => s.status === "active" && new Date(s.end_at).getTime() > now && s.remaining_cents > 0);
  const expired = (subs || []).filter((s) => !(s.status === "active" && new Date(s.end_at).getTime() > now && s.remaining_cents > 0));
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Overview</h1>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="card">
          <p className="text-sm text-gray-500">Account</p>
          <p className="mt-2 text-xl font-semibold">{me?.email || "—"}</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500">Wallet balance</p>
          <p className="mt-2 text-3xl font-bold text-brand-600">
            ${me ? (me.balance_cents / 100).toFixed(2) : "—"}
          </p>
          <p className="mt-1 text-xs text-gray-400">Used after subscriptions are drained.</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500">Active subscriptions</p>
          <p className="mt-2 text-3xl font-bold">{active.length}</p>
        </div>
      </div>

      <div className="card">
        <h2 className="mb-3 text-lg font-semibold">Subscriptions</h2>
        {active.length === 0 ? (
          <p className="text-sm text-gray-500">No active subscription. Requests will draw from your wallet balance.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-gray-500">
              <tr>
                <th className="py-2">Plan</th>
                <th>Remaining</th>
                <th>Expires</th>
                <th>Deduction order</th>
              </tr>
            </thead>
            <tbody>
              {active
                .slice()
                .sort((a, b) => new Date(a.end_at).getTime() - new Date(b.end_at).getTime())
                .map((s, i) => (
                  <tr key={s.id} className="border-t">
                    <td className="py-2">{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                    <td>${(s.remaining_cents / 100).toFixed(2)}</td>
                    <td>{new Date(s.end_at).toLocaleString()}</td>
                    <td>{i + 1}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
        {expired.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-gray-500">Past/exhausted ({expired.length})</summary>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {expired.map((s) => (
                  <tr key={s.id} className="border-t text-gray-500">
                    <td className="py-2">{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                    <td>${(s.remaining_cents / 100).toFixed(2)}</td>
                    <td>{new Date(s.end_at).toLocaleString()}</td>
                    <td>{s.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>

      <div className="card">
        <h2 className="mb-2 text-lg font-semibold">Quick start</h2>
        <pre className="overflow-x-auto rounded bg-gray-900 p-4 text-sm text-gray-100">
{`curl ${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/v1/chat/completions \\
  -H "Authorization: Bearer sk-xxx" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'`}
        </pre>
      </div>
    </div>
  );
}
