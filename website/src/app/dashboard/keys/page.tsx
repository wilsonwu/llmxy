"use client";
import { useState } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";

type Key = { id: number; name: string; key_prefix: string; status: string; used_cents: number; quota_cents: number };

export default function KeysPage() {
  const { data, mutate } = useSWR<Key[]>("/api/v1/api-keys", fetcher);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [err, setErr] = useState("");

  async function create() {
    setErr("");
    try {
      const r = await api<Key & { key: string }>("/api/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: name || "default" }),
      });
      setCreated(r.key);
      setName("");
      mutate();
    } catch (e: any) { setErr(e.message); }
  }

  async function del(id: number) {
    if (!confirm("确认删除？")) return;
    await api(`/api/v1/api-keys/${id}`, { method: "DELETE" });
    mutate();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">API Keys</h1>
      <div className="card">
        <div className="flex gap-2">
          <input className="input" placeholder="Key 名称" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn-primary" onClick={create}>创建</button>
        </div>
        {err && <p className="mt-2 text-sm text-red-600">{err}</p>}
        {created && (
          <div className="mt-4 rounded border border-amber-300 bg-amber-50 p-4 text-sm">
            <p className="mb-1 font-semibold">请妥善保存，只显示一次：</p>
            <code className="break-all">{created}</code>
          </div>
        )}
      </div>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead>
            <tr><th>名称</th><th>前缀</th><th>状态</th><th>已用</th><th>配额</th><th></th></tr>
          </thead>
          <tbody>
            {data?.map((k) => (
              <tr key={k.id}>
                <td>{k.name}</td>
                <td><code>{k.key_prefix}…</code></td>
                <td>{k.status}</td>
                <td>¥{(k.used_cents / 100).toFixed(4)}</td>
                <td>{k.quota_cents ? `¥${(k.quota_cents / 100).toFixed(2)}` : "无限制"}</td>
                <td><button className="text-red-600" onClick={() => del(k.id)}>删除</button></td>
              </tr>
            ))}
            {!data?.length && <tr><td colSpan={6} className="text-center text-gray-500">暂无 Key</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
