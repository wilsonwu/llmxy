"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Plan = {
  id: number;
  code: string;
  name: string;
  description?: string;
  price_cents: number;
  quota_cents: number;
  duration_days: number;
};

export default function PricingPage() {
  const { data, error } = useSWR<Plan[]>("/api/v1/plans", fetcher);
  if (error) return <p className="text-red-600">加载失败：{String(error)}</p>;
  if (!data) return <p>加载中...</p>;
  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">套餐</h1>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {data.map((p) => (
          <div key={p.id} className="card">
            <h3 className="text-xl font-semibold">{p.name}</h3>
            <p className="mt-1 text-sm text-gray-600">{p.description || "—"}</p>
            <p className="mt-4 text-3xl font-bold">¥{(p.price_cents / 100).toFixed(2)}</p>
            <p className="text-sm text-gray-500">额度 {(p.quota_cents / 100).toFixed(2)} 元 / {p.duration_days} 天</p>
            <a href="/dashboard/topup" className="btn-primary mt-4 w-full">订阅</a>
          </div>
        ))}
      </div>
    </div>
  );
}
