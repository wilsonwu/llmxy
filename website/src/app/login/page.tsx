"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPw] = useState("");
  const [err, setErr] = useState("");
  const router = useRouter();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const r = await api<{ access_token: string }>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
        skipAuth: true,
      });
      setToken(r.access_token);
      router.push("/dashboard/overview");
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <h1 className="mb-6 text-2xl font-bold">登录</h1>
      <form onSubmit={onSubmit} className="card space-y-4">
        <div>
          <label className="label">邮箱</label>
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label className="label">密码</label>
          <input className="input" type="password" value={password} onChange={(e) => setPw(e.target.value)} required />
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className="btn-primary w-full">登录</button>
        <p className="text-center text-sm text-gray-500">
          还没有账号？<a href="/register" className="text-brand-600">注册</a>
        </p>
      </form>
    </div>
  );
}
