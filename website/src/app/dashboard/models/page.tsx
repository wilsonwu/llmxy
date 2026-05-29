"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Model = { id: string; modality?: "chat" | "embedding" | "image"; strategy: string; target_count: number };
type Key = { id: number; name: string; key_prefix: string; status: string };
type EnvoyInst = { name: string; mode: string; listen_port: number; proxy_url: string };
type Transport = {
  direct: { available: boolean };
  envoy: { available: boolean; instances: EnvoyInst[] };
};
type Gateway = { id: string; label: string; url: string; hint?: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

type Tab = "chat" | "chat-stream" | "embeddings" | "image";

function buildCurl(tab: Tab, base: string, key: string, model: string) {
  const auth = `-H "Authorization: Bearer ${key}"`;
  const ct = `-H "Content-Type: application/json"`;
  if (tab === "embeddings") {
    const body = JSON.stringify({ model, input: "hello world" });
    return `curl ${base}/v1/embeddings \\\n  ${auth} \\\n  ${ct} \\\n  -d '${body}'`;
  }
  if (tab === "image") {
    const body = JSON.stringify({ model, prompt: "a red panda coding", n: 1, size: "1024x1024" });
    return `curl ${base}/v1/images/generations \\\n  ${auth} \\\n  ${ct} \\\n  -d '${body}'`;
  }
  const payload: Record<string, unknown> = {
    model,
    messages: [{ role: "user", content: "Hello!" }],
  };
  if (tab === "chat-stream") payload.stream = true;
  const body = JSON.stringify(payload);
  return `curl ${base}/v1/chat/completions \\\n  ${auth} \\\n  ${ct} \\\n  -d '${body}'`;
}

function buildJs(tab: Tab, base: string, key: string, model: string) {
  if (tab === "embeddings") {
    return `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "${key}",
  baseURL: "${base}/v1",
});

const res = await client.embeddings.create({
  model: "${model}",
  input: "hello world",
});
console.log(res.data[0].embedding.length);`;
  }
  if (tab === "image") {
    return `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "${key}",
  baseURL: "${base}/v1",
});

const res = await client.images.generate({
  model: "${model}",
  prompt: "a red panda coding",
  n: 1,
  size: "1024x1024",
});
console.log(res.data[0].url);`;
  }
  const streamFlag = tab === "chat-stream" ? "\n  stream: true," : "";
  return `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "${key}",
  baseURL: "${base}/v1",
});

const res = await client.chat.completions.create({
  model: "${model}",
  messages: [{ role: "user", content: "Hello!" }],${streamFlag}
});
console.log(res${tab === "chat-stream" ? "" : ".choices[0].message"});`;
}

function buildPy(tab: Tab, base: string, key: string, model: string) {
  if (tab === "embeddings") {
    return `from openai import OpenAI

client = OpenAI(api_key="${key}", base_url="${base}/v1")
res = client.embeddings.create(model="${model}", input="hello world")
print(len(res.data[0].embedding))`;
  }
  if (tab === "image") {
    return `from openai import OpenAI

client = OpenAI(api_key="${key}", base_url="${base}/v1")
res = client.images.generate(
    model="${model}",
    prompt="a red panda coding",
    n=1,
    size="1024x1024",
)
print(res.data[0].url)`;
  }
  const stream = tab === "chat-stream";
  return `from openai import OpenAI

client = OpenAI(api_key="${key}", base_url="${base}/v1")
res = client.chat.completions.create(
    model="${model}",
    messages=[{"role": "user", "content": "Hello!"}],${stream ? "\n    stream=True," : ""}
)
${stream ? "for chunk in res:\n    print(chunk.choices[0].delta.content or '', end='')" : "print(res.choices[0].message.content)"}`;
}

export default function ModelsPage() {
  const { data: models } = useSWR<Model[]>("/api/v1/models", fetcher);
  const { data: keys } = useSWR<Key[]>("/api/v1/api-keys", fetcher);
  const { data: transport } = useSWR<Transport>("/api/v1/relay/transport", fetcher, {
    refreshInterval: 15_000,
  });

  const [model, setModel] = useState<string>("");
  const [keyId, setKeyId] = useState<string>("");
  const [tab, setTab] = useState<Tab>("chat");
  const [lang, setLang] = useState<"curl" | "js" | "py">("curl");
  const [copied, setCopied] = useState(false);
  const [gatewayId, setGatewayId] = useState<string>("default");

  const gateways: Gateway[] = useMemo(() => {
    const list: Gateway[] = [
      { id: "default", label: "Default API", url: API_BASE, hint: "always available" },
    ];
    for (const inst of transport?.envoy.instances || []) {
      list.push({
        id: `envoy-${inst.name}`,
        label: `${inst.name} (${inst.mode})`,
        url: inst.proxy_url,
        hint: inst.mode === "local" ? "envoy on this host" : "remote envoy",
      });
    }
    return list;
  }, [transport]);

  const activeGateway =
    gateways.find((g) => g.id === gatewayId) || gateways[0];
  const activeBase = activeGateway?.url || API_BASE;

  const activeModel = model || models?.[0]?.id || "<model-name>";
  const activeModality = useMemo(() => {
    const m = (models || []).find((x) => x.id === activeModel);
    return m?.modality || "chat";
  }, [models, activeModel]);
  const activeKey = useMemo(() => {
    const k = (keys || []).find((x) => String(x.id) === keyId);
    if (k) return `${k.key_prefix}...`;
    return "sk-xxxxxxxx";
  }, [keys, keyId]);

  const filteredTabs: Tab[] =
    activeModality === "image"
      ? ["image"]
      : activeModality === "embedding"
      ? ["embeddings"]
      : ["chat", "chat-stream"];
  const effectiveTab: Tab = filteredTabs.includes(tab) ? tab : filteredTabs[0];

  const snippet = useMemo(() => {
    if (lang === "js") return buildJs(effectiveTab, activeBase, activeKey, activeModel);
    if (lang === "py") return buildPy(effectiveTab, activeBase, activeKey, activeModel);
    return buildCurl(effectiveTab, activeBase, activeKey, activeModel);
  }, [lang, effectiveTab, activeBase, activeKey, activeModel]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Models</h1>

      <div className="card">
        <h2 className="mb-2 text-lg font-semibold">Available models</h2>
        {!models ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : models.length === 0 ? (
          <p className="text-sm text-gray-500">No models published yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {models.map((m) => {
              const meaningful = m.target_count > 1;
              const label = meaningful
                ? m.strategy === "smart"
                  ? "auto-selected per prompt"
                  : m.strategy === "fallback"
                  ? `${m.target_count} providers, ordered fallback`
                  : `${m.target_count} providers, load-balanced`
                : "single provider";
              const modality = m.modality || "chat";
              const modBadge =
                modality === "image"
                  ? "bg-purple-100 text-purple-700"
                  : modality === "embedding"
                  ? "bg-teal-100 text-teal-700"
                  : "bg-blue-100 text-blue-700";
              return (
                <button
                  key={m.id}
                  onClick={() => setModel(m.id)}
                  className={`rounded border px-3 py-1.5 text-sm ${
                    (model || models[0].id) === m.id
                      ? "border-brand-600 bg-brand-50 text-brand-700"
                      : "border-gray-200 hover:bg-gray-50"
                  }`}
                  title={label}
                >
                  {m.id}
                  <span className={`ml-2 rounded px-1.5 py-0.5 text-xs ${modBadge}`}>{modality}</span>
                  <span className="ml-2 text-xs text-gray-400">{label}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="card space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-sm font-semibold">Try it</div>
          <select
            className="input w-auto"
            value={gatewayId}
            onChange={(e) => setGatewayId(e.target.value)}
            title="Pick which gateway to route through"
          >
            {gateways.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label} — {g.url}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            value={keyId}
            onChange={(e) => setKeyId(e.target.value)}
          >
            <option value="">Use placeholder (sk-xxx)</option>
            {(keys || []).filter((k) => k.status === "active").map((k) => (
              <option key={k.id} value={String(k.id)}>
                {k.name} — {k.key_prefix}…
              </option>
            ))}
          </select>
          <span className="text-xs text-gray-500">
            We only show the key prefix here; paste the full secret yourself.
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b pb-2 text-sm">
          <span className="mr-2 text-gray-500">Endpoint:</span>
          {filteredTabs.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded px-2 py-1 ${effectiveTab === t ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}
            >
              {t === "chat" ? "chat" : t === "chat-stream" ? "chat (stream)" : t === "image" ? "images" : "embeddings"}
            </button>
          ))}
          <span className="ml-4 text-gray-500">Lang:</span>
          {(["curl", "js", "py"] as const).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`rounded px-2 py-1 ${lang === l ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}
            >
              {l}
            </button>
          ))}
          <button onClick={copy} className="ml-auto rounded border px-2 py-1 text-xs hover:bg-gray-50">
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>

        <pre className="overflow-x-auto rounded bg-gray-900 p-4 text-sm text-gray-100">{snippet}</pre>

        <p className="text-xs text-gray-500">
          Base URL: <code>{activeBase}/v1</code> · Model: <code>{activeModel}</code>
          {activeGateway?.hint && (
            <span className="ml-1 text-gray-400">({activeGateway.hint})</span>
          )}
        </p>
      </div>
    </div>
  );
}
