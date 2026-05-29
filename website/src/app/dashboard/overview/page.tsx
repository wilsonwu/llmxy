"use client";
import useSWR, { mutate as globalMutate } from "swr";
import { api, fetcher } from "@/lib/api";
import { Badge, useToast } from "@/components/ui";

type Sub = {
  id: number;
  plan_id: number;
  plan_code?: string;
  plan_name?: string;
  plan_type?: "recurring" | "one_time";
  start_at: string;
  current_period_start: string;
  current_period_end: string;
  status: "active" | "past_due" | "canceled" | "expired";
  remaining_cents: number;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  last_renewal_at: string | null;
  last_renewal_error: string | null;
  depleted: boolean;
};

const statusTone: Record<Sub["status"], "success" | "warning" | "neutral"> = {
  active: "success",
  past_due: "warning",
  canceled: "neutral",
  expired: "neutral",
};

export default function Overview() {
  const { data: me } = useSWR<{ email: string; balance_cents: number; role: string }>("/api/v1/auth/me", fetcher);
  const { data: subs, mutate: mutateSubs } = useSWR<Sub[]>("/api/v1/usage/subscriptions", fetcher);
  const { toast, confirm } = useToast();
  const now = Date.now();
  const live = (subs || []).filter(
    (s) => (s.status === "active" || s.status === "past_due") && new Date(s.current_period_end).getTime() > now
  );
  const closed = (subs || []).filter((s) => !live.includes(s));

  async function cancelAtPeriodEnd(s: Sub) {
    if (!(await confirm({ title: "Cancel at period end", body: `Cancel ${s.plan_name || s.plan_code || `#${s.plan_id}`} at period end? You keep your remaining quota until ${new Date(s.current_period_end).toLocaleString()} and pay nothing more.`, confirmText: "Cancel at period end" }))) return;
    try {
      await api(`/api/v1/subscriptions/${s.id}/cancel?at_period_end=true`, { method: "POST" });
      mutateSubs();
      toast("Will cancel at period end", "success");
    } catch (e: any) { toast(e?.message || "cancel failed", "error"); }
  }

  async function cancelImmediate(s: Sub) {
    if (!(await confirm({ title: "Cancel immediately", body: `Cancel ${s.plan_name || s.plan_code || `#${s.plan_id}`} immediately? You lose access right away and get a refund prorated by unused quota.`, danger: true, confirmText: "Cancel + refund" }))) return;
    try {
      const r = await api<{ refund_cents: number; balance_cents: number }>(
        `/api/v1/subscriptions/${s.id}/cancel?at_period_end=false`,
        { method: "POST" }
      );
      toast(`Refunded $${(r.refund_cents / 100).toFixed(2)} — wallet $${(r.balance_cents / 100).toFixed(2)}`, "success");
      mutateSubs();
      globalMutate("/api/v1/auth/me");
    } catch (e: any) { toast(e?.message || "cancel failed", "error"); }
  }

  async function resume(s: Sub) {
    try {
      await api(`/api/v1/subscriptions/${s.id}/resume`, { method: "POST" });
      mutateSubs();
      toast("Subscription resumed", "success");
    } catch (e: any) { toast(e?.message || "resume failed", "error"); }
  }

  async function renewNow(s: Sub) {
    try {
      const r = await api<{ ok: boolean; status: string; reason: string }>(
        `/api/v1/subscriptions/${s.id}/renew`,
        { method: "POST" }
      );
      toast(`Renew: ${r.status} (${r.reason})`, r.ok ? "success" : "error");
      mutateSubs();
      globalMutate("/api/v1/auth/me");
    } catch (e: any) { toast(e?.message || "renew failed", "error"); }
  }

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
          <p className="mt-1 text-xs text-gray-400">Used after subscriptions are drained. Auto-charged on monthly renewal.</p>
        </div>
        <div className="card">
          <p className="text-sm text-gray-500">Active subscriptions</p>
          <p className="mt-2 text-3xl font-bold">{live.filter(s => s.status === "active" && !s.depleted).length}</p>
          {live.some(s => s.depleted) && (
            <p className="mt-1 text-xs text-orange-600">{live.filter(s => s.depleted).length} exhausted this cycle.</p>
          )}
          {live.some(s => s.status === "past_due") && (
            <p className="mt-1 text-xs text-amber-600">{live.filter(s => s.status === "past_due").length} past due — top up to resume.</p>
          )}
        </div>
      </div>

      <div className="card">
        <h2 className="mb-3 text-lg font-semibold">Subscriptions</h2>
        {live.length === 0 ? (
          <p className="text-sm text-gray-500">No active subscription. Requests will draw from your wallet balance.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-gray-500">
              <tr>
                <th className="py-2">Plan</th>
                <th>Status</th>
                <th>Remaining (this cycle)</th>
                <th>Period ends / renews</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {live
                .slice()
                .sort((a, b) => new Date(a.current_period_end).getTime() - new Date(b.current_period_end).getTime())
                .map((s) => (
                  <tr key={s.id} className="border-t align-top">
                    <td className="py-2">{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                    <td>
                      {s.depleted ? (
                        <Badge tone="warning">
                          {s.plan_type === "one_time" ? "exhausted" : "quota exhausted"}
                        </Badge>
                      ) : (
                        <Badge tone={statusTone[s.status]}>{s.status}</Badge>
                      )}
                      {s.depleted && s.plan_type !== "one_time" && (
                        <p className="mt-1 text-xs text-gray-500">refills on renewal</p>
                      )}
                      {s.cancel_at_period_end && (
                        <p className="mt-1 text-xs text-gray-500">cancels at period end</p>
                      )}
                      {s.last_renewal_error && (
                        <p className="mt-1 text-xs text-amber-600">{s.last_renewal_error}</p>
                      )}
                    </td>
                    <td>${(s.remaining_cents / 100).toFixed(2)}</td>
                    <td>
                      <div>{new Date(s.current_period_end).toLocaleString()}</div>
                      <div className="text-xs text-gray-500">
                        {s.plan_type === "one_time"
                          ? "expires (one-time)"
                          : s.cancel_at_period_end
                          ? "stops"
                          : s.status === "past_due"
                          ? "retry pending"
                          : "auto-renew"}
                      </div>
                    </td>
                    <td className="space-y-1 whitespace-nowrap">
                      {s.plan_type !== "one_time" && (
                        s.cancel_at_period_end ? (
                          <button className="text-sm text-brand-600 hover:underline" onClick={() => resume(s)}>
                            Resume
                          </button>
                        ) : (
                          <button className="text-sm text-gray-600 hover:underline" onClick={() => cancelAtPeriodEnd(s)}>
                            Cancel at period end
                          </button>
                        )
                      )}
                      <button className="block text-sm text-red-600 hover:underline" onClick={() => cancelImmediate(s)}>
                        Cancel + refund
                      </button>
                      {s.plan_type !== "one_time" && s.status === "past_due" && (
                        <button className="block text-sm text-brand-600 hover:underline" onClick={() => renewNow(s)}>
                          Renew now
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
        {closed.length > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-sm text-gray-500">Past/closed ({closed.length})</summary>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {closed.map((s) => (
                  <tr key={s.id} className="border-t text-gray-500">
                    <td className="py-2">{s.plan_name || s.plan_code || `#${s.plan_id}`}</td>
                    <td>{s.status}</td>
                    <td>ended {new Date(s.current_period_end).toLocaleString()}</td>
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
  -d '{"model":"<your-model>","messages":[{"role":"user","content":"hi"}]}'`}
        </pre>
        <p className="mt-2 text-xs text-gray-500">
          See <a href="/dashboard/models" className="text-brand-600 hover:underline">Models</a> for the live model list and copy-ready snippets (curl / JS / Python).
        </p>
      </div>
    </div>
  );
}
