"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";

export default function HeroCta() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    setAuthed(!!getToken());
  }, []);
  if (authed === null) {
    return <span className="btn-primary opacity-50">Get started free</span>;
  }
  return authed ? (
    <Link href="/dashboard/overview" className="btn-primary">Go to console</Link>
  ) : (
    <Link href="/register" className="btn-primary">Get started free</Link>
  );
}
