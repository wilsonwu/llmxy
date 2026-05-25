"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, setToken } from "@/lib/api";

export default function HeaderNav() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const router = useRouter();

  useEffect(() => {
    setAuthed(!!getToken());
    const onStorage = () => setAuthed(!!getToken());
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function logout() {
    setToken(null);
    setAuthed(false);
    router.push("/");
  }

  return (
    <nav className="flex items-center gap-4 text-sm">
      <Link href="/pricing" className="hover:text-brand-600">Pricing</Link>
      {authed ? (
        <>
          <Link href="/dashboard/overview" className="hover:text-brand-600">Console</Link>
          <button onClick={logout} className="hover:text-brand-600">Sign out</button>
        </>
      ) : authed === false ? (
        <>
          <Link href="/login" className="hover:text-brand-600">Sign in</Link>
          <Link href="/register" className="btn-primary !py-1 !px-3 text-xs">Sign up free</Link>
        </>
      ) : null}
    </nav>
  );
}
