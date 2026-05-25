"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type Inst = {
  id: number;
  name: string;
  listen_port: number;
  admin_port: number;
  status: "stopped" | "starting" | "running" | "error";
  pid: number | null;
  config_version: number;
  config_dir: string;
  log_dir: string;
  last_health_at: string | null;
  last_error: string | null;
};

const emptyForm = { name: "", listen_port: 9000, admin_port: 9001 };

const statusColor: Record<Inst["status"], string> = {
  running: "bg-green-100 text-green-700",
  stopped: "bg-gray-100 text-gray-600",
  starting: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
};

function TransportBanner({ instances }: { instances: Inst[] | undefined }) {
  const running = (instances || []).filter((i) => i.status === "running");
  if (running.length === 0) {
    return (
      <div className="card border-l-4 border-gray-400 bg-gray-50">
        <div className="font-medium">Active transport: api-direct (legacy)</div>
        <div className="text-sm text-gray-600">
          No envoy instances are running. All <code>/v1/*</code> traffic is handled by the api
          process on its own port. Start an envoy instance below to enable the high-performance
          C++ data path.
        </div>
      </div>
    );
  }
  const ports = running.map((i) => i.listen_port).join(", ");
  return (
    <div className="card border-l-4 border-green-500 bg-green-50">
      <div className="font-medium text-green-800">
        Active transport: envoy ({running.length} running)
      </div>
      <div className="text-sm text-gray-700">
        Point clients at the envoy listen port(s) <code>{ports}</code> for the high-perf path.
        The api-direct port (default <code>:8000</code>) also keeps serving <code>/v1/*</code>
        as a fallback — both transports are live; clients pick by URL.
      </div>
    </div>
  );
}

export default function EnvoyPage() {
  const { data, mutate } = useSWR<Inst[]>("/api/v1/admin/envoy/instances", fetcher, {
    refreshInterval: 5000,
  });
  const [creating, setCreating] = useState<typeof emptyForm | null>(null);
  const [drawer, setDrawer] = useState<{ inst: Inst; tab: "stats" | "logs" } | null>(null);
  const [drawerData, setDrawerData] = useState<any>(null);

  async function create() {
    if (!creating) return;
    await api("/api/v1/admin/envoy/instances", { method: "POST", body: JSON.stringify(creating) });
    setCreating(null);
    mutate();
  }

  async function act(id: number, op: "start" | "stop" | "restart" | "reload" | "regenerate-config") {
    try {
      await api(`/api/v1/admin/envoy/instances/${id}/${op}`, { method: "POST" });
    } catch (e: any) {
      alert(e.message || "operation failed");
    }
    mutate();
  }

  async function del(id: number) {
    if (!confirm("Delete this envoy instance? (must be stopped)")) return;
    try {
      await api(`/api/v1/admin/envoy/instances/${id}`, { method: "DELETE" });
    } catch (e: any) {
      alert(e.message);
    }
    mutate();
  }

  async function openDrawer(inst: Inst, tab: "stats" | "logs") {
    setDrawer({ inst, tab });
    setDrawerData(null);
    try {
      if (tab === "stats") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/stats`));
      } else {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/logs?tail=200`));
      }
    } catch (e: any) {
      setDrawerData({ error: e.message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Envoy instances</h1>
        <button className="btn-primary" onClick={() => setCreating({ ...emptyForm })}>
          New instance
        </button>
      </div>

      <TransportBanner instances={data} />

      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>ID</th><th>Name</th><th>Listen</th><th>Admin</th>
              <th>Status</th><th>PID</th><th>cfg v</th>
              <th>Last health</th><th>Last error</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(data || []).map((i) => (
              <tr key={i.id}>
                <td>{i.id}</td>
                <td className="font-medium">{i.name}</td>
                <td>{i.listen_port}</td>
                <td>{i.admin_port}</td>
                <td>
                  <span className={`rounded px-2 py-0.5 text-xs ${statusColor[i.status]}`}>{i.status}</span>
                </td>
                <td>{i.pid ?? "—"}</td>
                <td>{i.config_version}</td>
                <td className="text-xs text-gray-500">
                  {i.last_health_at ? new Date(i.last_health_at).toLocaleString() : "—"}
                </td>
                <td className="max-w-[200px] truncate text-xs text-red-600" title={i.last_error || ""}>
                  {i.last_error || ""}
                </td>
                <td className="space-x-1 whitespace-nowrap">
                  {i.status !== "running" && (
                    <button className="btn-outline" onClick={() => act(i.id, "start")}>Start</button>
                  )}
                  {i.status === "running" && (
                    <>
                      <button className="btn-outline" onClick={() => act(i.id, "reload")}>Reload</button>
                      <button className="btn-outline" onClick={() => act(i.id, "restart")}>Restart</button>
                      <button className="btn-outline" onClick={() => act(i.id, "stop")}>Stop</button>
                    </>
                  )}
                  <button className="btn-outline" onClick={() => act(i.id, "regenerate-config")}>Regen</button>
                  <button className="btn-outline" onClick={() => openDrawer(i, "stats")}>Stats</button>
                  <button className="btn-outline" onClick={() => openDrawer(i, "logs")}>Logs</button>
                  <button className="btn-danger" onClick={() => del(i.id)}>Del</button>
                </td>
              </tr>
            ))}
            {(!data || data.length === 0) && (
              <tr><td colSpan={10} className="text-center text-gray-500">No instances. Create one to get started.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[420px] space-y-3">
            <h2 className="text-lg font-semibold">New envoy instance</h2>
            <div>
              <label className="label">Name</label>
              <input
                className="input w-full"
                value={creating.name}
                onChange={(e) => setCreating({ ...creating, name: e.target.value })}
                placeholder="primary"
              />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="label">Listen port</label>
                <input
                  type="number"
                  className="input w-full"
                  value={creating.listen_port}
                  onChange={(e) => setCreating({ ...creating, listen_port: +e.target.value })}
                />
              </div>
              <div className="flex-1">
                <label className="label">Admin port</label>
                <input
                  type="number"
                  className="input w-full"
                  value={creating.admin_port}
                  onChange={(e) => setCreating({ ...creating, admin_port: +e.target.value })}
                />
              </div>
            </div>
            <p className="text-xs text-gray-500">
              The instance is created in stopped state. Click <em>Start</em> to spawn the envoy process.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setCreating(null)}>Cancel</button>
              <button className="btn-primary" onClick={create} disabled={!creating.name}>Create</button>
            </div>
          </div>
        </div>
      )}

      {drawer && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30" onClick={() => setDrawer(null)}>
          <div className="card max-h-[80vh] w-[800px] space-y-3 overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">
                {drawer.inst.name} — {drawer.tab}
              </h2>
              <button className="btn-outline" onClick={() => setDrawer(null)}>Close</button>
            </div>
            {drawerData === null && <div className="text-gray-500">Loading…</div>}
            {drawerData?.error && <div className="text-red-600">{drawerData.error}</div>}
            {drawer.tab === "stats" && drawerData?.counters && (
              <table className="table">
                <tbody>
                  {Object.entries(drawerData.counters as Record<string, number>).sort().map(([k, v]) => (
                    <tr key={k}>
                      <td className="font-mono text-xs">{k}</td>
                      <td className="text-right">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {drawer.tab === "logs" && drawerData?.lines && (
              <pre className="max-h-[60vh] overflow-auto rounded bg-gray-900 p-3 text-xs text-gray-100">
                {(drawerData.lines as string[]).join("\n")}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
