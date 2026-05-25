"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { getToken, setToken } from "@/lib/api";

const items = [
  { href: "/dashboard", label: "看板" },
  { href: "/dashboard/users", label: "用户" },
  { href: "/dashboard/channels", label: "上游通道" },
  { href: "/dashboard/models", label: "模型/倍率" },
  { href: "/dashboard/plans", label: "套餐" },
  { href: "/dashboard/routes", label: "智能路由" },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  useEffect(() => { if (!getToken()) router.replace("/login"); }, [router]);
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r bg-white">
        <div className="border-b px-5 py-4 text-lg font-bold text-brand-600">llmxy admin</div>
        <nav className="space-y-1 p-3">
          {items.map((i) => (
            <Link key={i.href} href={i.href}
              className={`block rounded px-3 py-2 text-sm ${pathname === i.href ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}>
              {i.label}
            </Link>
          ))}
          <button className="mt-4 block w-full rounded px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50"
            onClick={() => { setToken(null); router.push("/login"); }}>
            退出
          </button>
        </nav>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
