"use client";
import useSWR from "swr";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, fetcher } from "@/lib/api";

type Plan = {
  id: number;
  code: string;
  name: string;
  description?: string;
  plan_type: "recurring" | "one_time";
  price_cents: number;
  quota_cents: number;
  duration_days: number;
  max_purchases_per_user?: number | null;
};

type Sub = {
  id: number;
  plan_id: number;
  status: string;
  current_period_end: string;
  remaining_cents: number;
  depleted: boolean;
};

export default function PricingPage() {
  const { data, error } = useSWR<Plan[]>("/api/v1/plans", fetcher);
  const { data: subs, mutate: refetchSubs } = useSWR<Sub[]>(
    "/api/v1/usage/subscriptions",
    fetcher,
    { revalidateOnMount: true, revalidateOnFocus: true }
  );
  const router = useRouter();
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState<string>("");

  const now = Date.now();
  const liveSubs = (subs || []).filter(
    (s) => s.status === "active" && new Date(s.current_period_end).getTime() > now
  );
  const activePlanIds = new Set(liveSubs.map((s) => s.plan_id));
  const depletedPlanIds = new Set(liveSubs.filter((s) => s.depleted).map((s) => s.plan_id));
  const purchaseCounts = new Map<number, number>();
  for (const s of subs || []) {
    purchaseCounts.set(s.plan_id, (purchaseCounts.get(s.plan_id) || 0) + 1);
  }

  async function subscribe(p: Plan) {
    setBusy(p.id);
    setMsg("");
    try {
      const r = await api<{ ok: boolean; subscription_id: number; balance_cents: number }>(
        `/api/v1/plans/${p.id}/subscribe`,
        { method: "POST" }
      );
      setMsg(`Subscribed (#${r.subscription_id}). Remaining balance: $${(r.balance_cents / 100).toFixed(2)}`);
      refetchSubs();
      router.push("/dashboard/overview");
    } catch (e: any) {
      if (e?.status === 401) return; // api helper already redirects to /login
      if (e?.status === 409) {
        const code = e?.detail?.code;
        if (code === "purchase_limit_reached") {
          const d = e.detail;
          setMsg(`Purchase limit reached for this plan (${d.used}/${d.limit} used).`);
        } else {
          setMsg("You already have an active subscription to this plan.");
        }
        refetchSubs();
        return;
      }
      if (e?.status === 402) {
        const d = e.detail || {};
        const shortfall = ((d.shortfall_cents ?? p.price_cents) / 100).toFixed(2);
        setMsg(`Insufficient balance — $${shortfall} short. Redirecting to top-up…`);
        const qs = new URLSearchParams({
          plan_id: String(p.id),
          amount: String(Math.ceil((d.shortfall_cents ?? p.price_cents) / 100)),
        });
        router.push(`/dashboard/topup?${qs}`);
        return;
      }
      setMsg(e?.message || "subscribe failed");
    } finally {
      setBusy(null);
    }
  }

  if (error) return <p className="text-red-600">Failed to load: {String(error)}</p>;
  if (!data) return <p>Loading...</p>;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-3xl font-bold">Plans</h1>
      {msg && <p className="text-sm text-gray-700">{msg}</p>}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {data.map((p) => {
          const active = activePlanIds.has(p.id);
          const depleted = depletedPlanIds.has(p.id);
          const isOneTime = p.plan_type === "one_time";
          const limit = p.max_purchases_per_user ?? null;
          const used = purchaseCounts.get(p.id) || 0;
          const limitReached = isOneTime && limit != null && used >= limit;
          const disabled = busy === p.id || active || limitReached;
          let buttonLabel: string;
          if (busy === p.id) buttonLabel = "Processing…";
          else if (depleted) buttonLabel = isOneTime ? "Active — quota exhausted" : "Active — quota exhausted (refills on renewal)";
          else if (active) buttonLabel = isOneTime ? "Active — wait until expiry" : "Already subscribed";
          else if (limitReached) buttonLabel = `Purchase limit reached (${used}/${limit})`;
          else buttonLabel = "Subscribe";
          return (
          <div key={p.id} className="card">
            <h3 className="text-xl font-semibold">{p.name}</h3>
            <p className="mt-1 text-sm text-gray-600">{p.description || "—"}</p>
            <p className="mt-4 text-3xl font-bold">
              ${(p.price_cents / 100).toFixed(2)}
              {p.plan_type === "recurring" && <span className="text-base font-normal text-gray-500"> /month</span>}
            </p>
            <p className="text-sm text-gray-500">
              {p.plan_type === "recurring"
                ? `Quota $${(p.quota_cents / 100).toFixed(2)} / month · auto-renews on the 1st`
                : `Quota $${(p.quota_cents / 100).toFixed(2)} · expires in ${p.duration_days} days · one-time charge`}
            </p>
            {isOneTime && (
              <p className="text-xs text-gray-500 mt-1">
                {limit == null ? `Unlimited purchases · ${used} used` : `${used}/${limit} purchases used`}
              </p>
            )}
            <button
              className={`${disabled && busy !== p.id ? "btn-outline cursor-not-allowed opacity-70" : "btn-primary"} mt-4 w-full`}
              disabled={disabled}
              onClick={() => subscribe(p)}
            >
              {buttonLabel}
            </button>
          </div>
          );
        })}
      </div>
    </div>
  );
}
