"use client";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

export default function SettingsPage() {
  const { data: me } = useSWR<{ email: string; created_at: string; role: string }>("/api/v1/auth/me", fetcher);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Settings</h1>
      <div className="card space-y-2">
        <p><span className="text-gray-500">Email: </span>{me?.email}</p>
        <p><span className="text-gray-500">Role: </span>{me?.role}</p>
        <p><span className="text-gray-500">Registered: </span>{me ? new Date(me.created_at).toLocaleString() : ""}</p>
      </div>
      <p className="text-sm text-gray-500">Password change and OAuth binding coming soon.</p>
    </div>
  );
}
