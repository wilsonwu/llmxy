"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

export default function SettingsPage() {
  const { data: me } = useSWR<{ email: string; created_at: string; role: string }>("/api/v1/auth/me", fetcher);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">设置</h1>
      <div className="card space-y-2">
        <p><span className="text-gray-500">邮箱：</span>{me?.email}</p>
        <p><span className="text-gray-500">角色：</span>{me?.role}</p>
        <p><span className="text-gray-500">注册时间：</span>{me ? new Date(me.created_at).toLocaleString() : ""}</p>
      </div>
      <p className="text-sm text-gray-500">改密、绑定 OAuth 等功能后续提供。</p>
    </div>
  );
}
