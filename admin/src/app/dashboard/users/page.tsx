"use client";
import useSWR from "swr";
import { useState } from "react";
import { api, fetcher } from "@/lib/api";

type User = { id: number; email: string; role: string; balance_cents: number; status: string; created_at: string };

export default function UsersPage() {
  const [q, setQ] = useState("");
  const { data, mutate } = useSWR<{ items: User[]; total: number }>(`/api/v1/admin/users?page=1&page_size=50&q=${encodeURIComponent(q)}`, fetcher);

  async function toggle(u: User) {
    const path = u.status === "active" ? "disable" : "enable";
    await api(`/api/v1/admin/users/${u.id}/${path}`, { method: "POST" });
    mutate();
  }
  async function adjust(u: User) {
    const v = prompt(`为 ${u.email} 调整余额（分，可负）：`, "100");
    if (!v) return;
    await api(`/api/v1/admin/users/${u.id}/balance/adjust?amount_cents=${Number(v)}`, { method: "POST" });
    mutate();
  }
  async function reset(u: User) {
    const v = prompt(`为 ${u.email} 重置密码（≥6 位）：`);
    if (!v) return;
    await api(`/api/v1/admin/users/${u.id}/reset-password?new_password=${encodeURIComponent(v)}`, { method: "POST" });
    alert("已重置");
  }
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">用户管理</h1>
      <div className="flex gap-2">
        <input className="input" placeholder="搜索邮箱" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="card overflow-x-auto">
        <table className="table">
          <thead><tr><th>ID</th><th>邮箱</th><th>角色</th><th>余额</th><th>状态</th><th>注册</th><th></th></tr></thead>
          <tbody>
            {data?.items?.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>{u.email}</td>
                <td>{u.role}</td>
                <td>¥{(u.balance_cents/100).toFixed(2)}</td>
                <td>{u.status}</td>
                <td>{new Date(u.created_at).toLocaleString()}</td>
                <td className="space-x-2">
                  <button className="btn-outline" onClick={() => adjust(u)}>调整余额</button>
                  <button className="btn-outline" onClick={() => reset(u)}>改密</button>
                  <button className="btn-danger" onClick={() => toggle(u)}>{u.status === "active" ? "停用" : "启用"}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
