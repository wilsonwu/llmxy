"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";

const items = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/dashboard/users", label: "Users" },
  { href: "/dashboard/channels", label: "Upstream channels" },
  { href: "/dashboard/models", label: "Models / Rates" },
  { href: "/dashboard/plans", label: "Plans" },
  { href: "/dashboard/routes", label: "Smart routing" },
];

type Me = { email: string; role: string };

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    api<Me>("/api/v1/auth/me").then(setMe).catch(() => router.replace("/login"));
  }, [router]);

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r bg-white">
        <div className="border-b px-5 py-4">
          <div className="text-lg font-bold text-brand-600">llmxy admin</div>
          {me && (
            <div className="mt-1 truncate text-xs text-gray-500" title={me.email}>
              {me.email} · {me.role}
            </div>
          )}
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {items.map((i) => (
            <Link key={i.href} href={i.href}
              className={`block rounded px-3 py-2 text-sm ${pathname === i.href ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}>
              {i.label}
            </Link>
          ))}
        </nav>
        <button className="m-3 rounded border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50"
          onClick={() => { setToken(null); router.push("/login"); }}>
          Sign out
        </button>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}

