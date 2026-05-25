"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Tx = { id: number; type: string; amount_cents: number; balance_after: number; note?: string; created_at: string };

export default function BillingPage() {
  const { data } = useSWR<{ items: Tx[] }>("/api/v1/usage/balance-tx?page=1&page_size=50", fetcher);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">账单流水</h1>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>时间</th><th>类型</th><th>金额</th><th>余额</th><th>备注</th></tr></thead>
          <tbody>
            {data?.items?.map((t) => (
              <tr key={t.id}>
                <td>{new Date(t.created_at).toLocaleString()}</td>
                <td>{t.type}</td>
                <td className={t.amount_cents >= 0 ? "text-green-600" : "text-red-600"}>
                  {t.amount_cents >= 0 ? "+" : ""}¥{(t.amount_cents / 100).toFixed(2)}
                </td>
                <td>¥{(t.balance_after / 100).toFixed(2)}</td>
                <td>{t.note || "—"}</td>
              </tr>
            ))}
            {!data?.items?.length && <tr><td colSpan={5} className="text-center text-gray-500">暂无</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
