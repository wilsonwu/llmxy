"use client";
import { useState } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";

type QuotaMode = "until_depleted" | "periodic";
type QuotaPeriod = "day" | "week" | "month";
type Status = "active" | "disabled" | "expired";

type Key = {
  id: number;
  name: string;
  key_prefix: string;
  status: Status;
  used_cents: number;
  quota_cents: number;
  expires_at: string | null;
  quota_mode: QuotaMode;
  quota_period: QuotaPeriod | null;
  quota_period_start: string | null;
  quota_period_end: string | null;
};

type FormState = {
  id?: number;
  name: string;
  // dollars, "" = unlimited
  quota_dollars: string;
  // YYYY-MM-DDTHH:mm or "" = never
  expires_at_local: string;
  quota_mode: QuotaMode;
  quota_period: QuotaPeriod;
};

const emptyForm: FormState = {
  name: "",
  quota_dollars: "",
  expires_at_local: "",
  quota_mode: "until_depleted",
  quota_period: "month",
};

const statusBadge: Record<Status, string> = {
  active: "bg-green-100 text-green-700",
  disabled: "bg-gray-100 text-gray-600",
  expired: "bg-orange-100 text-orange-700",
};

function dollars(c: number) {
  return `$${(c / 100).toFixed(2)}`;
}

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleString();
}

function toIsoOrNull(localValue: string): string | null {
  if (!localValue) return null;
  // datetime-local has no timezone; treat as local time and serialise as ISO.
  return new Date(localValue).toISOString();
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function KeysPage() {
  const { data, mutate } = useSWR<Key[]>("/api/v1/api-keys", fetcher);
  const [editing, setEditing] = useState<FormState | null>(null);
  const [created, setCreated] = useState<string | null>(null);
  const [err, setErr] = useState("");

  function openCreate() {
    setErr("");
    setCreated(null);
    setEditing({ ...emptyForm });
  }
  function openEdit(k: Key) {
    setErr("");
    setCreated(null);
    setEditing({
      id: k.id,
      name: k.name,
      quota_dollars: k.quota_cents ? (k.quota_cents / 100).toString() : "",
      expires_at_local: toLocalInput(k.expires_at),
      quota_mode: k.quota_mode,
      quota_period: k.quota_period || "month",
    });
  }

  async function save() {
    if (!editing) return;
    setErr("");
    const dollars = editing.quota_dollars.trim();
    const quota_cents = dollars === "" ? 0 : Math.round(parseFloat(dollars) * 100);
    if (Number.isNaN(quota_cents) || quota_cents < 0) {
      setErr("Quota must be a non-negative number");
      return;
    }
    const expires_iso = toIsoOrNull(editing.expires_at_local);
    const period: QuotaPeriod | null = editing.quota_mode === "periodic" ? editing.quota_period : null;
    try {
      if (editing.id) {
        await api(`/api/v1/api-keys/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: editing.name,
            quota_cents,
            expires_at: expires_iso,
            clear_expires_at: expires_iso === null,
            quota_mode: editing.quota_mode,
            quota_period: period,
          }),
        });
        setEditing(null);
      } else {
        const r = await api<Key & { key: string }>(`/api/v1/api-keys`, {
          method: "POST",
          body: JSON.stringify({
            name: editing.name || "default",
            quota_cents,
            expires_at: expires_iso,
            quota_mode: editing.quota_mode,
            quota_period: period,
          }),
        });
        setCreated(r.key);
        setEditing(null);
      }
      mutate();
    } catch (e: any) {
      setErr(e?.message || "save failed");
    }
  }

  async function del(id: number) {
    if (!confirm("Delete this key? Requests using it will start failing immediately.")) return;
    try {
      await api(`/api/v1/api-keys/${id}`, { method: "DELETE" });
      mutate();
    } catch (e: any) { setErr(e?.message || "delete failed"); }
  }

  async function toggle(k: Key) {
    setErr("");
    const action = k.status === "active" ? "disable" : "enable";
    try {
      await api(`/api/v1/api-keys/${k.id}/${action}`, { method: "POST" });
      mutate();
    } catch (e: any) {
      if (e?.detail?.code === "extend_expires_at_first") {
        setErr("This key has expired. Edit it and set a future expiration before re-enabling.");
      } else {
        setErr(e?.message || `${action} failed`);
      }
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">API Keys</h1>
        <button className="btn-primary" onClick={openCreate}>New key</button>
      </div>

      {err && <p className="text-sm text-red-600">{err}</p>}
      {created && (
        <div className="rounded border border-amber-300 bg-amber-50 p-4 text-sm">
          <p className="mb-1 font-semibold">Save this key — it will only be shown once:</p>
          <code className="break-all">{created}</code>
          <button className="ml-3 text-xs text-gray-600 underline" onClick={() => setCreated(null)}>dismiss</button>
        </div>
      )}

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Status</th>
              <th>Quota</th>
              <th>Mode</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data?.map((k) => {
              const limited = k.quota_cents > 0;
              const pct = limited ? Math.min(100, Math.round((k.used_cents / k.quota_cents) * 100)) : 0;
              return (
                <tr key={k.id} className="align-top">
                  <td className="font-medium">{k.name}</td>
                  <td><code className="text-xs">{k.key_prefix}…</code></td>
                  <td>
                    <span className={`rounded px-2 py-0.5 text-xs ${statusBadge[k.status]}`}>{k.status}</span>
                  </td>
                  <td className="min-w-[180px]">
                    <div className="text-xs text-gray-700">
                      {dollars(k.used_cents)}{limited ? ` / ${dollars(k.quota_cents)}` : " / unlimited"}
                    </div>
                    {limited && (
                      <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-gray-200">
                        <div
                          className={`h-full ${pct >= 100 ? "bg-red-500" : pct >= 80 ? "bg-amber-500" : "bg-green-500"}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    )}
                  </td>
                  <td>
                    {k.quota_mode === "periodic" ? (
                      <div>
                        <div className="text-xs">periodic · {k.quota_period}</div>
                        {k.quota_period_end && (
                          <div className="text-xs text-gray-500">resets {fmtDate(k.quota_period_end)}</div>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs">until depleted</span>
                    )}
                  </td>
                  <td className="text-xs">{fmtDate(k.expires_at)}</td>
                  <td className="space-x-2 whitespace-nowrap">
                    <button className="text-sm text-brand-600 hover:underline" onClick={() => openEdit(k)}>Edit</button>
                    <button className="text-sm text-gray-600 hover:underline" onClick={() => toggle(k)}>
                      {k.status === "active" ? "Disable" : "Enable"}
                    </button>
                    <button className="text-sm text-red-600 hover:underline" onClick={() => del(k.id)}>Delete</button>
                  </td>
                </tr>
              );
            })}
            {!data?.length && (
              <tr><td colSpan={7} className="text-center text-gray-500">No keys yet</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30" onClick={() => setEditing(null)}>
          <div className="card w-[520px] space-y-3" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold">{editing.id ? "Edit key" : "New key"}</h2>

            <div>
              <label className="label">Name</label>
              <input
                className="input w-full"
                placeholder="e.g. production-backend"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </div>

            <div>
              <label className="label">Expiration <span className="text-xs text-gray-500">(blank = never)</span></label>
              <input
                type="datetime-local"
                className="input w-full"
                value={editing.expires_at_local}
                onChange={(e) => setEditing({ ...editing, expires_at_local: e.target.value })}
              />
            </div>

            <div>
              <label className="label">Quota (USD) <span className="text-xs text-gray-500">(blank = unlimited)</span></label>
              <input
                type="number"
                min={0}
                step="0.01"
                className="input w-full"
                placeholder="e.g. 50"
                value={editing.quota_dollars}
                onChange={(e) => setEditing({ ...editing, quota_dollars: e.target.value })}
              />
            </div>

            <div>
              <label className="label">Quota mode</label>
              <div className="flex gap-4 text-sm">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    checked={editing.quota_mode === "until_depleted"}
                    onChange={() => setEditing({ ...editing, quota_mode: "until_depleted" })}
                  />
                  Until depleted
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    checked={editing.quota_mode === "periodic"}
                    onChange={() => setEditing({ ...editing, quota_mode: "periodic" })}
                  />
                  Periodic refresh
                </label>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {editing.quota_mode === "until_depleted"
                  ? "Quota counts until exhausted, then the key blocks requests until you raise it."
                  : "Used quota resets to $0 at the start of every period."}
              </p>
            </div>

            {editing.quota_mode === "periodic" && (
              <div>
                <label className="label">Refresh period</label>
                <select
                  className="input w-full"
                  value={editing.quota_period}
                  onChange={(e) => setEditing({ ...editing, quota_period: e.target.value as QuotaPeriod })}
                >
                  <option value="day">Day (rolling 24h)</option>
                  <option value="week">Week (rolling 7d)</option>
                  <option value="month">Month (1st of next calendar month)</option>
                </select>
                {editing.id && (
                  <p className="mt-1 text-xs text-amber-600">
                    Changing mode or period resets the current period's used amount to $0.
                  </p>
                )}
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={save}>{editing.id ? "Save" : "Create"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
