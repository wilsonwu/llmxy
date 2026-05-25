"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";

export default function IndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? "/dashboard" : "/login");
  }, [router]);
  return null;
}
