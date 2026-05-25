"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPw] = useState("");
  const [err, setErr] = useState("");
  const router = useRouter();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      const r = await api<{ access_token: string }>("/api/v1/auth/register", {
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
      <h1 className="mb-6 text-2xl font-bold">注册</h1>
      <form onSubmit={onSubmit} className="card space-y-4">
        <div>
          <label className="label">邮箱</label>
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label className="label">密码 (≥ 6 位)</label>
          <input className="input" type="password" minLength={6} value={password} onChange={(e) => setPw(e.target.value)} required />
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className="btn-primary w-full">免费注册</button>
      </form>
    </div>
  );
}
