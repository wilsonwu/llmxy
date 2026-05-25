export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const TOKEN_KEY = "llmxy_admin_token";

export function getToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (typeof window === "undefined") return;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function api<T = any>(path: string, opts: RequestInit & { skipAuth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as any),
  };
  if (!opts.skipAuth) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...opts, headers });
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== "undefined" && !path.includes("/auth/")) {
      window.location.href = "/login";
    }
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j?.detail || j?.message || msg; } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const fetcher = (p: string) => api(p);
