"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

export default function AdminLogin() {
  const [email, setEmail] = useState("");
  const [password, setPw] = useState("");
  const [err, setErr] = useState("");
  const router = useRouter();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const r = await api<{ access_token: string; role: string }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
        skipAuth: true,
      });
      if (r.role !== "admin") { setErr("需要管理员账号"); return; }
      setToken(r.access_token);
      router.push("/dashboard");
    } catch (e: any) { setErr(e.message); }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-100">
      <form onSubmit={onSubmit} className="card w-96 space-y-4">
        <h1 className="text-xl font-bold">llmxy 管理后台</h1>
        <div>
          <label className="label">邮箱</label>
          <input className="input w-full" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label className="label">密码</label>
          <input className="input w-full" type="password" value={password} onChange={(e) => setPw(e.target.value)} required />
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className="btn-primary w-full">登录</button>
      </form>
    </div>
  );
}
