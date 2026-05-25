"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { getToken, setToken } from "@/lib/api";

const items = [
  { href: "/dashboard/overview", label: "概览" },
  { href: "/dashboard/keys", label: "API Key" },
  { href: "/dashboard/usage", label: "用量" },
  { href: "/dashboard/billing", label: "账单" },
  { href: "/dashboard/topup", label: "充值" },
  { href: "/dashboard/settings", label: "设置" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!getToken()) router.replace("/login");
  }, [router]);

  return (
    <div className="flex gap-6">
      <aside className="w-48 shrink-0">
        <nav className="space-y-1">
          {items.map((i) => (
            <Link
              key={i.href}
              href={i.href}
              className={`block rounded px-3 py-2 text-sm ${pathname === i.href ? "bg-brand-50 text-brand-700 font-semibold" : "hover:bg-gray-100"}`}
            >
              {i.label}
            </Link>
          ))}
          <button
            onClick={() => { setToken(null); router.push("/login"); }}
            className="mt-4 block w-full rounded px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50"
          >
            退出登录
          </button>
        </nav>
      </aside>
      <div className="flex-1">{children}</div>
    </div>
  );
}
