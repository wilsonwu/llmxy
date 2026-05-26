"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type P = { id?: number; code: string; name: string; description?: string; plan_type: "recurring" | "one_time"; price_cents: number; quota_cents: number; duration_days: number; max_purchases_per_user?: number | null; active: boolean };
const empty: P = { code: "", name: "", description: "", plan_type: "recurring", price_cents: 0, quota_cents: 0, duration_days: 30, max_purchases_per_user: null, active: true };

export default function PlansPage() {
  const { data, mutate } = useSWR<P[]>("/api/v1/admin/plans", fetcher);
  const [editing, setEditing] = useState<P | null>(null);

  async function save(p: P) {
    // recurring plans ignore max_purchases_per_user; strip it so the server
    // doesn't see stale UI state if the operator switched type mid-edit.
    const payload = p.plan_type === "recurring" ? { ...p, max_purchases_per_user: null } : p;
    if (p.id) await api(`/api/v1/admin/plans/${p.id}`, { method: "PUT", body: JSON.stringify(payload) });
    else await api(`/api/v1/admin/plans`, { method: "POST", body: JSON.stringify(payload) });
    setEditing(null); mutate();
  }
  async function del(id: number) {
    if (!confirm("Delete this plan?")) return;
    await api(`/api/v1/admin/plans/${id}`, { method: "DELETE" }); mutate();
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Plans</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty })}>New</button>
      </div>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>Name</th><th>Type</th><th>Price</th><th>Quota</th><th>Duration</th><th>Limit</th><th>Active</th><th></th></tr></thead>
          <tbody>
            {data?.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td><td>{p.code}</td><td>{p.name}</td>
                <td>{p.plan_type === "one_time" ? "one-time" : "monthly"}</td>
                <td>${(p.price_cents/100).toFixed(2)}{p.plan_type === "recurring" && <span className="text-xs text-gray-500"> /mo</span>}</td>
                <td>${(p.quota_cents/100).toFixed(2)}</td>
                <td>{p.plan_type === "one_time" ? `${p.duration_days}d` : "—"}</td>
                <td>{p.plan_type === "one_time" ? (p.max_purchases_per_user == null ? "∞" : `${p.max_purchases_per_user}×`) : "—"}</td>
                <td>{p.active ? "✓" : "—"}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => setEditing({ ...p })}>Edit</button>
                  <button className="btn-danger" onClick={() => del(p.id!)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[500px] space-y-3">
            <h2 className="text-lg font-semibold">{editing.id ? "Edit" : "New"} plan</h2>
            {(["code", "name", "description"] as const).map((k) => (
              <div key={k}><label className="label">{k}</label>
                <input className="input w-full" value={(editing as any)[k] || ""} onChange={(e) => setEditing({ ...editing, [k]: e.target.value })} /></div>
            ))}
            <div>
              <label className="label">type</label>
              <select className="input w-full" value={editing.plan_type} onChange={(e) => setEditing({ ...editing, plan_type: e.target.value as "recurring" | "one_time" })}>
                <option value="recurring">recurring — charged every month, quota resets on the 1st</option>
                <option value="one_time">one-time — single charge, expires after N days</option>
              </select>
            </div>
            <div className="flex gap-3">
              <div className="flex-1"><label className="label">price (cents){editing.plan_type === "recurring" ? " / month" : ""}</label>
                <input type="number" className="input w-full" value={editing.price_cents} onChange={(e) => setEditing({ ...editing, price_cents: +e.target.value })} /></div>
              <div className="flex-1"><label className="label">quota (cents){editing.plan_type === "recurring" ? " / cycle" : ""}</label>
                <input type="number" className="input w-full" value={editing.quota_cents} onChange={(e) => setEditing({ ...editing, quota_cents: +e.target.value })} /></div>
              {editing.plan_type === "one_time" && (
                <div className="flex-1"><label className="label">duration (days)</label>
                  <input type="number" className="input w-full" value={editing.duration_days} onChange={(e) => setEditing({ ...editing, duration_days: +e.target.value })} /></div>
              )}
            </div>
            {editing.plan_type === "one_time" && (
              <div>
                <label className="label">max purchases per user (blank = unlimited)</label>
                <input
                  type="number"
                  min={1}
                  className="input w-full"
                  value={editing.max_purchases_per_user ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    setEditing({ ...editing, max_purchases_per_user: v === "" ? null : Math.max(1, +v) });
                  }}
                />
                <p className="text-xs text-gray-500 mt-1">Counts all historical purchases (active/expired/canceled). Use 1 for free trials.</p>
              </div>
            )}
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active
            </label>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={() => save(editing)}>Save</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
