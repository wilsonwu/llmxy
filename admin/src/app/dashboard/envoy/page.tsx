"use client";
import useSWR from "swr";
import { useEffect, useState } from "react";
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
  proxy_url: string;
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
  host: string;
  listen_port: number;
  admin_port: number;
};

const emptyForm: CreateForm = {
  name: "",
  mode: "local",
  host: "",
  listen_port: 9000,
  admin_port: 9001,
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
          Local: {local.length} running — {local.map((i) => i.proxy_url).join(", ")}
        </div>
      )}
      {remote.length > 0 && (
        <div className="text-sm text-gray-700">
          Remote: {remote.length} registered ({remote.filter(remoteOnline).length} live) — {remote.filter(remoteOnline).map((i) => i.proxy_url).join(", ") || "none online"}
        </div>
      )}
    </div>
  );
}

function remoteOnline(i: Inst): boolean {
  if (!i.last_seen_at) return false;
  return Date.now() - new Date(i.last_seen_at).getTime() < 30_000;
}

function MenuItem({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export default function EnvoyPage() {
  const { data, mutate } = useSWR<Inst[]>("/api/v1/admin/envoy/instances", fetcher, {
    refreshInterval: 5000,
  });
  const [creating, setCreating] = useState<CreateForm | null>(null);
  const [editing, setEditing] = useState<{ id: number; mode: Mode; name: string; host: string; listen_port: number; admin_port: number } | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [openMenu, setOpenMenu] = useState<{ id: number; top: number; right: number } | null>(null);
  const [drawer, setDrawer] = useState<{ inst: Inst; tab: "stats" | "logs" | "conn" | "deploy" } | null>(null);
  const [drawerData, setDrawerData] = useState<any>(null);
  const [deploySubTab, setDeploySubTab] = useState<"k8s" | "docker" | "bootstrap">("k8s");
  // For the create dialog: which deployment method the operator picked, and a
  // popup window for inspecting the full YAML / command (kept separate from
  // the existing `drawer` which is for already-created instances).
  const [createDeployMethod, setCreateDeployMethod] = useState<"k8s" | "docker">("k8s");
  const [yamlPopup, setYamlPopup] = useState<{ title: string; body: string } | null>(null);
  // Inline preview of deploy manifests shown directly in the create dialog
  // for remote mode. Refetches whenever name/ports change so the node_id and
  // ports baked into the YAML always match what the create button will save.
  const [previewData, setPreviewData] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (!creating || creating.mode !== "remote" || !creating.name) {
      setPreviewData(null);
      setPreviewError(null);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const r: any = await api("/api/v1/admin/envoy/manifests/preview", {
          method: "POST",
          body: JSON.stringify({ name: creating.name }),
          signal: ctrl.signal,
        });
        setPreviewData(r);
      } catch (e: any) {
        if (e.name !== "AbortError") setPreviewError(e.message || "preview failed");
      } finally {
        setPreviewLoading(false);
      }
    }, 300);
    return () => { ctrl.abort(); clearTimeout(t); };
  }, [creating?.mode, creating?.name]);

  async function create() {
    if (!creating) return;
    if (creating.mode === "remote" && !creating.host) {
      alert("Host is required for remote mode. Deploy envoy first using the manifest below, then fill in the reachable address.");
      return;
    }
    const body: any = {
      name: creating.name,
      mode: creating.mode,
      listen_port: creating.listen_port,
      admin_port: creating.admin_port,
    };
    if (creating.mode === "remote") body.host = creating.host;
    try {
      await api("/api/v1/admin/envoy/instances", { method: "POST", body: JSON.stringify(body) });
      setCreating(null);
      setTestResult(null);
      setPreviewData(null);
      await mutate();
    } catch (e: any) {
      alert(e.message || "create failed");
    }
  }

  async function testConn() {
    if (!creating || creating.mode !== "remote" || !creating.host || !creating.admin_port) return;
    setTesting(true);
    setTestResult(null);
    try {
      const admin_url = `http://${creating.host}:${creating.admin_port}`;
      const r: any = await api("/api/v1/admin/envoy/test-connection", {
        method: "POST",
        body: JSON.stringify({ admin_url }),
      });
      setTestResult({
        ok: r.ok,
        msg: r.ok
          ? `OK — /ready returned 200 in ${r.latency_ms}ms`
          : r.error || `failed (HTTP ${r.status_code ?? "?"})`,
      });
    } catch (e: any) {
      setTestResult({ ok: false, msg: e.message || "test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function saveEdit() {
    if (!editing) return;
    const body: any = { name: editing.name, listen_port: editing.listen_port, admin_port: editing.admin_port };
    if (editing.mode === "remote") body.host = editing.host;
    try {
      await api(`/api/v1/admin/envoy/instances/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setEditing(null);
      mutate();
    } catch (e: any) {
      alert(e.message || "update failed");
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

  async function openDrawer(inst: Inst, tab: "stats" | "logs" | "conn" | "deploy") {
    setDrawer({ inst, tab });
    setDrawerData(null);
    setDeploySubTab("k8s");
    try {
      if (tab === "stats") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/stats`));
      } else if (tab === "logs") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/logs?tail=200`));
      } else if (tab === "conn") {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/connection`));
      } else {
        setDrawerData(await api(`/api/v1/admin/envoy/instances/${inst.id}/manifests`));
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
              <th>Entry URL</th><th>Status</th><th>cfg v</th>
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
                <td>
                  <button
                    className="font-mono text-xs text-blue-600 hover:underline"
                    title="Click to copy"
                    onClick={() => { navigator.clipboard.writeText(i.proxy_url); }}
                  >
                    {i.proxy_url}
                  </button>
                </td>
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
                    <button className="btn-primary" onClick={() => act(i.id, "start")}>Start</button>
                  )}
                  {i.mode === "local" && i.status === "running" && (
                    <button className="btn-outline" onClick={() => act(i.id, "stop")}>Stop</button>
                  )}
                  {i.mode === "remote" && (
                    <button className="btn-primary" onClick={() => act(i.id, "reload")}>Push</button>
                  )}
                  <span className="relative inline-block">
                    <button
                      className="btn-outline px-2"
                      onClick={(e) => {
                        if (openMenu?.id === i.id) { setOpenMenu(null); return; }
                        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                        // Position via fixed coords so the table's overflow-x-auto
                        // can't clip the menu.
                        setOpenMenu({ id: i.id, top: r.bottom + 4, right: window.innerWidth - r.right });
                      }}
                    >⋯</button>
                    {openMenu?.id === i.id && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setOpenMenu(null)} />
                        <div
                          className="fixed z-50 min-w-[140px] rounded border bg-white py-1 shadow-lg"
                          style={{ top: openMenu.top, right: openMenu.right }}
                        >
                          {i.mode === "local" && i.status === "running" && (
                            <>
                              <MenuItem onClick={() => { act(i.id, "reload"); setOpenMenu(null); }}>Reload</MenuItem>
                              <MenuItem onClick={() => { act(i.id, "restart"); setOpenMenu(null); }}>Restart</MenuItem>
                            </>
                          )}
                          <MenuItem onClick={() => { act(i.id, "regenerate-config"); setOpenMenu(null); }}>Regenerate config</MenuItem>
                          <MenuItem onClick={() => { openDrawer(i, "stats"); setOpenMenu(null); }}>Stats</MenuItem>
                          {i.mode === "local" && (
                            <MenuItem onClick={() => { openDrawer(i, "logs"); setOpenMenu(null); }}>Logs</MenuItem>
                          )}
                          {i.mode === "remote" && (
                            <>
                              <MenuItem onClick={() => { openDrawer(i, "conn"); setOpenMenu(null); }}>Connection</MenuItem>
                              <MenuItem onClick={() => { openDrawer(i, "deploy"); setOpenMenu(null); }}>Deploy manifests</MenuItem>
                            </>
                          )}
                          <div className="my-1 border-t" />
                          <MenuItem onClick={() => {
                            // Prefill host from existing admin_url so remote edits
                            // round-trip cleanly without forcing the operator to
                            // retype the address.
                            let host = "";
                            if (i.admin_url) {
                              try { host = new URL(i.admin_url).hostname; } catch { host = ""; }
                            }
                            setEditing({
                              id: i.id, mode: i.mode, name: i.name,
                              host,
                              listen_port: i.listen_port,
                              admin_port: i.admin_port || 0,
                            });
                            setOpenMenu(null);
                          }}>Edit</MenuItem>
                        </div>
                      </>
                    )}
                  </span>
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
          <div className={`card max-h-[90vh] space-y-3 overflow-auto ${creating.mode === "remote" ? "w-[720px]" : "w-[460px]"}`}>
            <h2 className="text-lg font-semibold">New envoy instance</h2>
            <div>
              <label className="label">Mode</label>
              <select
                className="input w-full"
                value={creating.mode}
                onChange={(e) => {
                  const newMode = e.target.value as Mode;
                  // Defaults: local binds these ports directly on this host
                  // (9000/9001). Remote: the form holds the EXTERNAL reachable
                  // ports — for k8s the NodePort defaults to 30000/30001;
                  // operator overrides if their deploy uses something else.
                  setCreating({
                    ...creating,
                    mode: newMode,
                    listen_port: newMode === "remote" ? 30000 : 9000,
                    admin_port: newMode === "remote" ? 30001 : 9001,
                  });
                }}
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
              {creating.mode === "remote" && (
                <p className="mt-1 text-xs text-gray-500">
                  node_id is derived as <code>llmxy-remote-{creating.name || "<name>"}</code> and baked into the manifest.
                </p>
              )}
            </div>

            {creating.mode === "remote" && (
              <>
                <div>
                  <label className="label">Deployment method</label>
                  <div className="flex gap-2">
                    {(["k8s", "docker"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setCreateDeployMethod(m)}
                        className={`flex-1 rounded border px-3 py-2 text-sm ${
                          createDeployMethod === m
                            ? "border-blue-600 bg-blue-50 font-medium text-blue-700"
                            : "border-gray-300 text-gray-700 hover:bg-gray-50"
                        }`}
                      >
                        {m === "k8s" ? "Kubernetes" : "Docker"}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  <div className="font-medium">
                    Step 1 — deploy envoy via {createDeployMethod === "k8s" ? "Kubernetes" : "Docker"}
                  </div>
                  {!creating.name && (
                    <p className="mt-2 text-xs">Enter a name above to generate the manifest.</p>
                  )}
                  {creating.name && previewLoading && <p className="mt-2 text-xs">Generating manifest…</p>}
                  {previewError && <p className="mt-2 text-xs text-red-700">{previewError}</p>}
                  {previewData && !previewError && (
                    <>
                      {createDeployMethod === "k8s" ? (
                        <ol className="ml-5 mt-1 list-decimal space-y-0.5 text-xs">
                          <li>Click <b>View YAML</b> below and copy the full manifest.</li>
                          <li>Apply it: <code>kubectl apply -f -</code> (paste, then Ctrl-D).</li>
                          <li>Wait for the pod to be ready. It will dial xDS at{" "}
                            <code>{previewData.control_plane_host}:{previewData.xds_port}</code>{" "}
                            and ALS at <code>:{previewData.als_port}</code>.</li>
                          <li>
                            The Service exposes envoy as NodePort{" "}
                            <b>{previewData.k8s_listen_nodeport}</b> (listen) /{" "}
                            <b>{previewData.k8s_admin_nodeport}</b> (admin) — those are the
                            external ports clients outside the cluster hit. Fill them into
                            the <b>Listen / Admin port</b> fields below (pre-filled by
                            default), along with the node IP or Ingress hostname as <b>Host</b>.
                            If your deploy maps NodePort differently, type the actual values.
                          </li>
                        </ol>
                      ) : (
                        <ol className="ml-5 mt-1 list-decimal space-y-0.5 text-xs">
                          <li>Click <b>View command</b> below and copy the shell snippet.</li>
                          <li>Run it on the host where envoy should live (writes bootstrap.yaml then <code>docker run</code>).</li>
                          <li>Envoy will dial xDS at{" "}
                            <code>{previewData.control_plane_host}:{previewData.xds_port}</code>{" "}
                            and ALS at <code>:{previewData.als_port}</code>.</li>
                          <li>
                            With <code>--network=host</code>, envoy is reachable on the docker
                            host at <b>9000</b> (listen) / <b>9001</b> (admin) — fill those
                            into the port fields below along with the host's LAN IP / hostname.
                          </li>
                        </ol>
                      )}
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <div className="text-xs">
                          node_id: <code>{previewData.node_id}</code>
                        </div>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="btn-outline"
                            onClick={() => setYamlPopup({
                              title: createDeployMethod === "k8s"
                                ? "Kubernetes manifest (kubectl apply -f -)"
                                : "Docker run script",
                              body: createDeployMethod === "k8s" ? previewData.k8s_yaml : previewData.docker_run,
                            })}
                          >
                            {createDeployMethod === "k8s" ? "View YAML" : "View command"}
                          </button>
                          <button
                            type="button"
                            className="btn-outline"
                            onClick={() => {
                              const text = createDeployMethod === "k8s" ? previewData.k8s_yaml : previewData.docker_run;
                              navigator.clipboard.writeText(text);
                            }}
                          >
                            Copy
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>

                <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  <div className="font-medium">Step 2 — register the reachable address</div>
                  <p className="mt-1 text-xs">
                    Once envoy is up, paste where it's reachable. Submitting probes <code>/ready</code> and flips status to <b>online</b>.
                  </p>
                </div>
                <div>
                  <label className="label">Host <span className="text-xs text-red-600">(required)</span></label>
                  <div className="flex gap-2">
                    <input
                      className="input flex-1"
                      value={creating.host}
                      onChange={(e) => {
                        setCreating({ ...creating, host: e.target.value });
                        setTestResult(null);
                      }}
                      placeholder="envoy.example.com or 10.0.0.5"
                    />
                    <button
                      className="btn-outline whitespace-nowrap"
                      onClick={testConn}
                      disabled={!creating.host || !creating.admin_port || testing}
                    >
                      {testing ? "Testing…" : "Test"}
                    </button>
                  </div>
                  {testResult && (
                    <p className={`mt-1 text-xs ${testResult.ok ? "text-green-700" : "text-red-600"}`}>
                      {testResult.ok ? "✓ " : "✗ "}{testResult.msg}
                    </p>
                  )}
                </div>
              </>
            )}

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
                  onChange={(e) => {
                    setCreating({ ...creating, admin_port: +e.target.value });
                    setTestResult(null);
                  }}
                />
              </div>
            </div>
            {creating.mode === "remote" && (
              <p className="text-xs text-gray-500">
                Externally reachable ports — what clients OUTSIDE the cluster / host hit.
                For Kubernetes the manifest's Service exposes NodePort 30000 (listen) /
                30001 (admin); for <code>docker --network=host</code>, envoy binds 9000 / 9001
                directly on the host. If your deploy maps to different external ports
                (custom NodePort, ingress, port-mapping), type those instead.
              </p>
            )}

            {creating.mode === "local" && (
              <p className="text-xs text-gray-500">
                Created in stopped state. Click Start to spawn the envoy subprocess.
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => { setCreating(null); setTestResult(null); setPreviewData(null); }}>Cancel</button>
              <button
                className="btn-primary"
                onClick={create}
                disabled={!creating.name || (creating.mode === "remote" && !creating.host)}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {yamlPopup && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40" onClick={() => setYamlPopup(null)}>
          <div className="card max-h-[85vh] w-[860px] space-y-3 overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{yamlPopup.title}</h2>
              <div className="space-x-2">
                <button
                  className="btn-outline"
                  onClick={() => navigator.clipboard.writeText(yamlPopup.body)}
                >
                  Copy
                </button>
                <button className="btn-outline" onClick={() => setYamlPopup(null)}>Close</button>
              </div>
            </div>
            <pre className="max-h-[70vh] overflow-auto rounded bg-gray-900 p-3 text-xs text-gray-100">
              {yamlPopup.body}
            </pre>
          </div>
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/30">
          <div className="card w-[460px] space-y-3">
            <h2 className="text-lg font-semibold">Edit envoy instance</h2>
            <div>
              <label className="label">Mode</label>
              <input className="input w-full bg-gray-100" value={editing.mode} disabled />
              <p className="mt-1 text-xs text-gray-500">Mode is immutable. Delete and recreate to switch.</p>
            </div>
            <div>
              <label className="label">Name</label>
              <input
                className="input w-full"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </div>
            {editing.mode === "remote" && (
              <div>
                <label className="label">Host</label>
                <input
                  className="input w-full"
                  value={editing.host}
                  onChange={(e) => setEditing({ ...editing, host: e.target.value })}
                  placeholder="envoy.example.com or 10.0.0.5"
                />
                <p className="mt-1 text-xs text-gray-500">
                  Admin URL will be <code>http://{editing.host || "host"}:{editing.admin_port}</code>.
                  Saving re-probes /ready and updates status immediately.
                </p>
              </div>
            )}
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="label">Listen port</label>
                <input
                  type="number"
                  className="input w-full"
                  value={editing.listen_port}
                  onChange={(e) => setEditing({ ...editing, listen_port: +e.target.value })}
                />
              </div>
              <div className="flex-1">
                <label className="label">Admin port</label>
                <input
                  type="number"
                  className="input w-full"
                  value={editing.admin_port}
                  onChange={(e) => setEditing({ ...editing, admin_port: +e.target.value })}
                />
              </div>
            </div>
            {editing.mode === "local" && (
              <p className="text-xs text-amber-700">
                Port changes only take effect after restart.
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button className="btn-outline" onClick={() => setEditing(null)}>Cancel</button>
              <button className="btn-primary" onClick={saveEdit} disabled={!editing.name}>Save</button>
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
                {drawer.tab === "deploy" && drawerData && !drawerData.error && (
                  <button
                    className="btn-outline"
                    onClick={() => {
                      const text =
                        deploySubTab === "k8s" ? drawerData.k8s_yaml
                        : deploySubTab === "docker" ? drawerData.docker_run
                        : drawerData.bootstrap_yaml;
                      navigator.clipboard.writeText(text);
                    }}
                  >
                    Copy {deploySubTab}
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
            {drawer.tab === "deploy" && drawerData && !drawerData.error && (
              <>
                <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                  <div className="font-medium">Quick start</div>
                  <ol className="ml-5 mt-1 list-decimal space-y-0.5 text-xs">
                    <li>Copy the manifest below and <code>kubectl apply -f -</code> (or run the docker command).</li>
                    <li>Wait for the pod / container to be ready — envoy will dial xDS at{" "}
                      <code>{drawerData.control_plane_host}:{drawerData.xds_port}</code> and ALS at{" "}
                      <code>:{drawerData.als_port}</code>.</li>
                    <li>
                      Edit this instance and set <b>Host</b> to where the envoy is reachable
                      (NodePort host IP, Service IP, or Ingress hostname). Status flips to <b>online</b> once
                      it streams its first request.
                    </li>
                  </ol>
                  <div className="mt-2 text-xs">
                    node_id: <code>{drawerData.node_id}</code> · listen :{drawer.inst.listen_port} · admin :{drawer.inst.admin_port || 9901}
                  </div>
                </div>
                <div className="flex gap-1 border-b text-sm">
                  {(["k8s", "docker", "bootstrap"] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => setDeploySubTab(t)}
                      className={`px-3 py-1.5 ${deploySubTab === t ? "border-b-2 border-blue-600 font-medium text-blue-700" : "text-gray-600"}`}
                    >
                      {t === "k8s" ? "Kubernetes" : t === "docker" ? "Docker" : "bootstrap.yaml"}
                    </button>
                  ))}
                </div>
                <pre className="max-h-[55vh] overflow-auto rounded bg-gray-900 p-3 text-xs text-gray-100">
                  {deploySubTab === "k8s" ? drawerData.k8s_yaml
                    : deploySubTab === "docker" ? drawerData.docker_run
                    : drawerData.bootstrap_yaml}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
