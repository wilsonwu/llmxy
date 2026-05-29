"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { EmptyState, TableSkeleton } from "@/components/ui";

type Tx = { id: number; type: string; amount_cents: number; balance_after: number; note?: string; created_at: string };

export default function BillingPage() {
  const { data, isLoading } = useSWR<{ items: Tx[] }>("/api/v1/usage/balance-tx?page=1&page_size=50", fetcher);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Billing history</h1>
      <div className="card overflow-x-auto p-0">
        <table className="table">
          <thead><tr><th>Time</th><th>Type</th><th>Amount</th><th>Balance</th><th>Note</th></tr></thead>
          <tbody>
            {isLoading && <TableSkeleton cols={5} />}
            {!isLoading && data?.items?.map((t) => (
              <tr key={t.id}>
                <td>{new Date(t.created_at).toLocaleString()}</td>
                <td>{t.type}</td>
                <td className={t.amount_cents >= 0 ? "text-green-600" : "text-red-600"}>
                  {t.amount_cents >= 0 ? "+" : "-"}${Math.abs(t.amount_cents / 100).toFixed(2)}
                </td>
                <td>${(t.balance_after / 100).toFixed(2)}</td>
                <td>{t.note || "—"}</td>
              </tr>
            ))}
            {!isLoading && !data?.items?.length && (
              <tr><td colSpan={5}><EmptyState title="No billing records yet" hint="Charges and top-ups will show here as they happen." /></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
