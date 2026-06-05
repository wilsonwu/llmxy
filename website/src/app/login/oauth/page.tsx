"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { setToken } from "@/lib/api";

export default function OAuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState("");

  useEffect(() => {
    const raw = window.location.hash ? window.location.hash.slice(1) : window.location.search.slice(1);
    const params = new URLSearchParams(raw);
    const token = params.get("access_token") || params.get("token");
    const oauthError = params.get("error");

    if (token) {
      setToken(token);
      window.history.replaceState(null, "", "/login/oauth");
      router.replace("/dashboard/overview");
      return;
    }
    setError(oauthError || "OAuth sign in failed");
  }, [router]);

  return (
    <div className="mx-auto max-w-md">
      <div className="card space-y-4 text-center">
        <h1 className="text-2xl font-bold">Signing in</h1>
        {error ? (
          <>
            <p className="text-sm text-red-600">{error}</p>
            <Link className="btn-outline w-full" href="/login">Back to sign in</Link>
          </>
        ) : (
          <p className="text-sm text-gray-500">Completing OAuth sign in...</p>
        )}
      </div>
    </div>
  );
}