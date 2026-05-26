"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type Mode = "local" | "remote";

type Inst = {
  id: number;
  name: string;
  mode: Mode;
  node_id: string;
  listen_port: number;
  admin_port: number | null;
  admin_url: string | null;
  status: "stopped" | "starting" | "running" | "error";
  pid: number | null;
  config_version: number;
  config_dir: string | null;
  log_dir: string | null;
  last_health_at: string | null;
  last_error: string | null;
  last_seen_at: string | null;
  last_xds_version: string | null;
};

type CreateForm = {
  name: string;
  mode: Mode;
  listen_port: number;
  admin_port: number;
  admin_url: string;
};

const emptyForm: CreateForm = {
  name: "",
  mode: "local",
  listen_port: 9000,
  admin_port: 9001,
  admin_url: "",
};

const statusColor: Record<Inst["status"], string> = {
  running: "bg-green-100 text-green-700",
  stopped: "bg-gray-100 text-gray-600",
  starting: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
};

function TransportBanner({ instances }: { instances: Inst[] | undefined }) {
  const local = (instances || []).filter((i) => i.mode === "local" && i.status === "running");
  const remote = (instances || []).filter((i) => i.mode === "remote");
  if (local.length === 0 && remote.length === 0) {
    return (
      <div className="card border-l-4 border-gray-400 bg-gray-50">
        <div className="font-medium">Active transport: api-direct (legacy)</div>
        <div className="text-sm text-gray-600">
          No envoy instances. All <code>/v1/*</code> traffic is handled by the api process itself.
        </div>
      </div>
    );
  }
  return (
    <div className="card border-l-4 border-green-500 bg-green-50 space-y-1">
      <div className="font-medium text-green-800">Active transport: envoy</div>
      {local.length > 0 && (
        <div className="text-sm text-gray-700">
          Local: {local.length} running on port(s) {local.map((i) => i.listen_port).join(", ")}.
        </div>
      )}
      {remote.length > 0 && (
        <div className="text-sm text-gray-700">
          Remote: {remote.length} registered ({remote.filter(remoteOnline).length} live).
        </div>
      )}
    </div>
  );
}

function remoteOnline(i: Inst): boolean {
  if (!i.last_seen_at) return false;
  return Date.now() - new Date(i.last_seen_at).getTime() < 30_000;
}

export default function EnvoyPage() {
  const { data, mutate } = useSWR<Inst[]>("/api/v1/admin/envoy/instances", fetcher, {
    refreshInterval: 5000,
  });
  const [creating, setCreating] = useState<CreateForm | null>(null);
  const [drawer, setDrawer] = useState<{ inst: Inst; tab: "stats" | "logs" | "conn" | "bootstrap" } | null>(null);
  const [drawerData, setDrawerData] = useState<any>(null);

  async function create() {
    if (!creating) return;
    const body: any = {
      name: creating.name,
      mode: creating.mode,
      listen_port: creating.listen_port,
    };
    if (creating.mode === "local") {
      body.admin_port = creating.admin_port;
    } else {
      body.admin_url = creating.admin_url;
    }
    try {
      await api("/api/v1/admin/envoy/instances", { method: "POST", body: JSON.stringify(body) });
      setCreating(null);
      mutate();
    } catch (e: any) {
      alert(e.message || "create failed");
    }
  }

  async function act(id: number, op: string) {
    try {
      await api(`/api/v1/admin/envoy/instances/${id}/${op}`, { method: "POST" });
    } catch (e: any) {
      alert(e.message || "operation failed");
    }
    mutate();
  }

  async function del(id: number) {
    if (!confirm("Delete this envoy instance? (local instances must be stopped first)")) return;
    try {
      await api(`/api/v1/admin/envoy/instances/${id}`, { method: "DELETE" });
    } catch (e: any) {
      alert(e.message);
    }
    mutate();
  }

  async function openDrawer(inst: Inst, tab: "stats" | "logs" | "conn" | "bootstrap") {
    setDrawer({ inst, tab });
    setDrawerData(null);
    try {
      if (tab === "stats") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/stats`));
      } else if (tab === "logs") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/logs?tail=200`));
      } else if (tab === "conn") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/connection`));
      } else {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/bootstrap-template`));
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
              <th>ID</th><th>Name</th><th>Mode</th><th>Node id</th>
              <th>Listen</th><th>Status</th><th>cfg v</th>
              <th>Last seen / health</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(data || []).map((i) => (
              <tr key={i.id}>
                <td>{i.id}</td>
                <td className="font-medium">{i.name}</td>
                <td>
                  <span className={`rounded px-2 py-0.5 text-xs ${i.mode === "remote" ? "bg-purple-100 text-purple-700" : "bg-blue-100 text-blue-700"}`}>
                    {i.mode}
                  </span>
                </td>
                <td className="font-mono text-xs">{i.node_id}</td>
                <td>{i.listen_port}</td>
                <td>
                  {i.mode === "remote" ? (
                    <span className={`rounded px-2 py-0.5 text-xs ${remoteOnline(i) ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>
                      {remoteOnline(i) ? "online" : "offline"}
                    </span>
                  ) : (
                    <span className={`rounded px-2 py-0.5 text-xs ${statusColor[i.status]}`}>{i.status}</span>
                  )}
                </td>
                <td>{i.config_version}</td>
                <td className="text-xs text-gray-500">
                  {(i.last_seen_at || i.last_health_at) ? new Date(i.last_seen_at || i.last_health_at!).toLocaleString() : "—"}
                </td>
                <td className="space-x-1 whitespace-nowrap">
                  {i.mode === "local" && i.status !== "running" && (
                    <button className="btn-outline" onClick={() => act(i.id, "start")}>Start</button>
                  )}
                  {i.mode === "local" && i.status === "running" && (
                    <>
                      <button className="btn-outline" onClick={() => act(i.id, "reload")}>Reload</button>
                      <button className="btn-outline" onClick={() => act(i.id, "restart")}>Restart</button>
                      <button className="btn-outline" onClick={() => act(i.id, "stop")}>Stop</button>
                    </>
                  )}
                  {i.mode === "remote" && (
                    <>
                      <button className="btn-outline" onClick={() => act(i.id, "reload")}>Push</button>
                      <button className="btn-outline" onClick={() => openDrawer(i, "bootstrap")}>Bootstrap</button>
                      <button className="btn-outline" onClick={() => openDrawer(i, "conn")}>Conn</button>
                    </>
                  )}
                  <button className="btn-outline" onClick={() => act(i.id, "regenerate-config")}>Regen</button>
                  <button className="btn-outline" onClick={() => openDrawer(i, "stats")}>Stats</button>
                  {i.mode === "local" && (
                    <button className="btn-outline" onClick={() => openDrawer(i, "logs")}>Logs</button>
                  )}
                  <button className="btn-danger" onClick={() => del(i.id)}>Del</button>
                </td>
              </tr>
            ))}
            {(!data || data.length === 0) && (
              <tr><td colSpan={9} className="text-center text-gray-500">No instances. Create one to get started.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[460px] space-y-3">
            <h2 className="text-lg font-semibold">New envoy instance</h2>
            <div>
              <label className="label">Mode</label>
              <select
                className="input w-full"
                value={creating.mode}
                onChange={(e) => setCreating({ ...creating, mode: e.target.value as Mode })}
              >
                <option value="local">local — managed subprocess on this host</option>
                <option value="remote">remote — envoy deployed elsewhere, connects via xDS</option>
              </select>
            </div>
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
              {creating.mode === "local" && (
                <div className="flex-1">
                  <label className="label">Admin port</label>
                  <input
                    type="number"
                    className="input w-full"
                    value={creating.admin_port}
                    onChange={(e) => setCreating({ ...creating, admin_port: +e.target.value })}
                  />
                </div>
              )}
            </div>
            {creating.mode === "remote" && (
              <div>
                <label className="label">Admin URL</label>
                <input
                  className="input w-full"
                  value={creating.admin_url}
                  onChange={(e) => setCreating({ ...creating, admin_url: e.target.value })}
                  placeholder="http://envoy.example.com:9901"
                />
                <p className="mt-1 text-xs text-gray-500">
                  How the control plane reaches this envoy's admin API (for stats / readiness).
                  We'll probe it once at create time; failure is non-fatal.
                </p>
              </div>
            )}
            <p className="text-xs text-gray-500">
              {creating.mode === "local"
                ? "Created in stopped state. Click Start to spawn the envoy subprocess."
                : "After creating, click Bootstrap to copy the envoy bootstrap.yaml, paste it onto your envoy host, and run `envoy -c bootstrap.yaml`."}
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setCreating(null)}>Cancel</button>
              <button
                className="btn-primary"
                onClick={create}
                disabled={!creating.name || (creating.mode === "remote" && !creating.admin_url)}
              >
                Create
              </button>
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
              <div className="space-x-2">
                {drawer.tab === "bootstrap" && drawerData?.yaml && (
                  <button
                    className="btn-outline"
                    onClick={() => navigator.clipboard.writeText(drawerData.yaml)}
                  >
                    Copy
                  </button>
                )}
                <button className="btn-outline" onClick={() => setDrawer(null)}>Close</button>
              </div>
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
            {drawer.tab === "conn" && drawerData && !drawerData.error && (
              <table className="table">
                <tbody>
                  <tr><td>Node id</td><td className="font-mono text-xs">{drawerData.node_id}</td></tr>
                  <tr><td>ADS connected</td><td>{drawerData.ads_connected ? "yes" : "no"}</td></tr>
                  <tr><td>Last seen</td><td>{drawerData.last_seen_at ? new Date(drawerData.last_seen_at).toLocaleString() : "—"}</td></tr>
                  <tr><td>Last xDS version</td><td className="font-mono text-xs">{drawerData.last_xds_version || "—"}</td></tr>
                </tbody>
              </table>
            )}
            {drawer.tab === "bootstrap" && drawerData?.yaml && (
              <>
                <p className="text-sm text-gray-600">
                  Save this as <code>bootstrap.yaml</code> on your envoy host, then run{" "}
                  <code>envoy -c bootstrap.yaml</code>. The node will appear online here once it connects.
                </p>
                <pre className="max-h-[60vh] overflow-auto rounded bg-gray-900 p-3 text-xs text-gray-100">
                  {drawerData.yaml}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
