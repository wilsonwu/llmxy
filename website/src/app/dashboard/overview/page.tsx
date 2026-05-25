"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

export default function Overview() {
  const { data: me } = useSWR<{ email: string; balance_cents: number; role: string }>("/api/v1/auth/me", fetcher);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">概览</h1>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="card">
          <p className="text-sm text-gray-500">账户</p>
          <p className="mt-2 text-xl font-semibold">{me?.email || "—"}</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500">余额</p>
          <p className="mt-2 text-3xl font-bold text-brand-600">
            ¥{me ? (me.balance_cents / 100).toFixed(2) : "—"}
          </p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500">角色</p>
          <p className="mt-2 text-xl font-semibold">{me?.role || "—"}</p>
        </div>
      </div>
      <div className="card">
        <h2 className="mb-2 text-lg font-semibold">快速接入</h2>
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
