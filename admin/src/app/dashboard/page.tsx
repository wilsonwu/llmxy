"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Stats = { users_total: number; api_keys_total: number; requests_today: number; cost_today_cents: number; cost_total_cents: number };

export default function Dashboard() {
  const { data } = useSWR<Stats>("/api/v1/admin/stats", fetcher);
  const cards = [
    { label: "Users", val: data?.users_total ?? "-" },
    { label: "API Keys", val: data?.api_keys_total ?? "-" },
    { label: "Requests today", val: data?.requests_today ?? "-" },
    { label: "Spend today", val: data ? `$${(data.cost_today_cents/100).toFixed(2)}` : "-" },
    { label: "Spend total", val: data ? `$${(data.cost_total_cents/100).toFixed(2)}` : "-" },
  ];
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {cards.map((c) => (
          <div key={c.label} className="card">
            <p className="text-xs text-gray-500">{c.label}</p>
            <p className="mt-2 text-2xl font-bold">{c.val}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
