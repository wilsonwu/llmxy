"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";
import { Badge, EmptyState, Modal, TableSkeleton, useToast } from "@/components/ui";

type P = { id?: number; code: string; name: string; description?: string; plan_type: "recurring" | "one_time"; price_cents: number; quota_cents: number; duration_days: number; max_purchases_per_user?: number | null; active: boolean };
const empty: P = { code: "", name: "", description: "", plan_type: "recurring", price_cents: 0, quota_cents: 0, duration_days: 30, max_purchases_per_user: null, active: true };

export default function PlansPage() {
  const { data, mutate, isLoading } = useSWR<P[]>("/api/v1/admin/plans", fetcher);
  const [editing, setEditing] = useState<P | null>(null);
  const { toast, confirm } = useToast();

  async function save(p: P) {
    const payload = p.plan_type === "recurring" ? { ...p, max_purchases_per_user: null } : p;
    try {
      if (p.id) await api(`/api/v1/admin/plans/${p.id}`, { method: "PUT", body: JSON.stringify(payload) });
      else await api(`/api/v1/admin/plans`, { method: "POST", body: JSON.stringify(payload) });
      setEditing(null);
      mutate();
      toast(p.id ? "Plan updated" : "Plan created", "success");
    } catch (e: any) {
      toast(e?.message || "Save failed", "error");
    }
  }
  async function del(id: number, name: string) {
    if (!(await confirm({ title: "Delete plan", body: `Delete "${name}"? Active subscriptions keep running until they expire.`, danger: true, confirmText: "Delete" }))) return;
    try {
      await api(`/api/v1/admin/plans/${id}`, { method: "DELETE" });
      mutate();
      toast("Plan deleted", "success");
    } catch (e: any) { toast(e?.message || "Delete failed", "error"); }
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Plans</h1>
        <button className="btn-primary" onClick={() => setEditing({ ...empty })}>New</button>
      </div>
      <div className="card overflow-x-auto p-0">
        <table className="table">
          <thead><tr><th>ID</th><th>code</th><th>Name</th><th>Type</th><th>Price</th><th>Quota</th><th>Duration</th><th>Limit</th><th>Active</th><th></th></tr></thead>
          <tbody>
            {isLoading && <TableSkeleton cols={10} />}
            {!isLoading && data?.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td><td>{p.code}</td><td className="font-medium">{p.name}</td>
                <td>{p.plan_type === "one_time" ? <Badge tone="purple">one-time</Badge> : <Badge tone="info">monthly</Badge>}</td>
                <td>${(p.price_cents/100).toFixed(2)}{p.plan_type === "recurring" && <span className="text-xs text-gray-500"> /mo</span>}</td>
                <td>${(p.quota_cents/100).toFixed(2)}</td>
                <td>{p.plan_type === "one_time" ? `${p.duration_days}d` : "—"}</td>
                <td>{p.plan_type === "one_time" ? (p.max_purchases_per_user == null ? "∞" : `${p.max_purchases_per_user}×`) : "—"}</td>
                <td>{p.active ? <Badge tone="success">on</Badge> : <Badge tone="neutral">off</Badge>}</td>
                <td className="space-x-2 whitespace-nowrap">
                  <button className="btn-outline" onClick={() => setEditing({ ...p })}>Edit</button>
                  <button className="btn-danger" onClick={() => del(p.id!, p.name)}>Delete</button>
                </td>
              </tr>
            ))}
            {!isLoading && !data?.length && (
              <tr><td colSpan={10}><EmptyState title="No plans yet" hint="Define subscription tiers that users can subscribe to on the pricing page." /></td></tr>
            )}
          </tbody>
        </table>
      </div>
      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`${editing?.id ? "Edit" : "New"} plan`}
        width="w-[520px]"
        footer={
          <>
            <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary" onClick={() => editing && save(editing)}>Save</button>
          </>
        }
      >
        {editing && (
          <>
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
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active
            </label>
          </>
        )}
      </Modal>
    </div>
  );
}
