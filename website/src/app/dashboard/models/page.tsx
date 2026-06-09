"use client";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type ClientProtocol = "openai.chat" | "openai.responses" | "anthropic.messages";
type Model = { id: string; modality?: "chat" | "embedding" | "image"; exposed_protocols?: ClientProtocol[]; strategy: string; target_count: number };
type Key = { id: number; name: string; key_prefix: string; status: string };
type EnvoyInst = { name: string; mode: string; listen_port: number; proxy_url: string };
type Transport = {
  direct: { available: boolean };
  envoy: { available: boolean; instances: EnvoyInst[] };
};
type Gateway = { id: string; label: string; url: string; hint?: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

type Tab = "chat" | "chat-stream" | "embeddings" | "image";

function tabsForModality(modality?: Model["modality"]): Tab[] {
  if (modality === "image") return ["image"];
  if (modality === "embedding") return ["embeddings"];
  return ["chat", "chat-stream"];
}

function routeProtocols(model?: Model): ClientProtocol[] {
  const raw = model?.exposed_protocols?.length ? model.exposed_protocols : ["openai.chat"];
  const normalized = Array.from(new Set(raw.map(normalizeClientProtocol).filter((p): p is ClientProtocol => Boolean(p))));
  return normalized.length ? normalized : ["openai.chat"];
}

function normalizeClientProtocol(protocol: string): ClientProtocol {
  if (protocol === "openai") return "openai.chat";
  if (protocol === "anthropic") return "anthropic.messages";
  if (protocol === "openai.chat" || protocol === "openai.responses" || protocol === "anthropic.messages") return protocol;
  return "openai.chat";
}

function protocolLabel(protocol: ClientProtocol) {
  if (protocol === "openai.chat") return "OpenAI Chat";
  if (protocol === "openai.responses") return "OpenAI Responses";
  return "Anthropic Messages";
}

function endpointForTab(tab: Tab, protocol: ClientProtocol) {
  if (protocol === "anthropic.messages") return "/v1/messages";
  if (protocol === "openai.responses") return "/v1/responses";
  if (tab === "embeddings") return "/v1/embeddings";
  if (tab === "image") return "/v1/images/generations";
  return "/v1/chat/completions";
}

function labelForTab(tab: Tab) {
  if (tab === "chat-stream") return "chat stream";
  if (tab === "embeddings") return "embedding";
  if (tab === "image") return "image";
  return "chat";
}

function buildCurlCommand(url: string, headers: string[], body: Record<string, unknown>) {
  return [
    `curl ${url}`,
    ...headers.map((header) => `  -H "${header}"`),
    `  -d '${JSON.stringify(body)}'`,
  ].join(" \\\n");
}

function buildCurl(tab: Tab, protocol: ClientProtocol, base: string, key: string, model: string) {
  if (protocol === "anthropic.messages") {
    const body: Record<string, unknown> = { model, max_tokens: 1024, messages: [{ role: "user", content: "Hello!" }] };
    if (tab === "chat-stream") body.stream = true;
    return buildCurlCommand(`${base}/v1/messages`, [
      `x-api-key: ${key}`,
      "anthropic-version: 2023-06-01",
      "Content-Type: application/json",
    ], body);
  }
  if (protocol === "openai.responses") {
    const body: Record<string, unknown> = { model, input: "Hello!" };
    if (tab === "chat-stream") body.stream = true;
    return buildCurlCommand(`${base}/v1/responses`, [`Authorization: Bearer ${key}`, "Content-Type: application/json"], body);
  }
  const headers = [`Authorization: Bearer ${key}`, "Content-Type: application/json"];
  if (tab === "embeddings") {
    return buildCurlCommand(`${base}/v1/embeddings`, headers, { model, input: "hello world" });
  }
  if (tab === "image") {
    return buildCurlCommand(`${base}/v1/images/generations`, headers, { model, prompt: "a red panda coding", n: 1, size: "1024x1024" });
  }
  const payload: Record<string, unknown> = {
    model,
    messages: [{ role: "user", content: "Hello!" }],
  };
  if (tab === "chat-stream") payload.stream = true;
  return buildCurlCommand(`${base}/v1/chat/completions`, headers, payload);
}

function buildJs(tab: Tab, protocol: ClientProtocol, base: string, key: string, model: string) {
  if (protocol === "anthropic.messages") {
    const stream = tab === "chat-stream";
    return `import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  apiKey: "${key}",
  baseURL: "${base}/v1",
});

const res = await client.messages.create({
  model: "${model}",
  max_tokens: 1024,
  messages: [{ role: "user", content: "Hello!" }],${stream ? "\n  stream: true," : ""}
});
${stream ? "for await (const event of res) console.log(event);" : "console.log(res.content[0].text);"}`;
  }
  if (protocol === "openai.responses") {
    const stream = tab === "chat-stream";
    return `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "${key}",
  baseURL: "${base}/v1",
});

const res = await client.responses.create({
  model: "${model}",
  input: "Hello!",${stream ? "\n  stream: true," : ""}
});
${stream ? "for await (const event of res) console.log(event);" : "console.log(res.output_text);"}`;
  }
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

function buildPy(tab: Tab, protocol: ClientProtocol, base: string, key: string, model: string) {
  if (protocol === "anthropic.messages") {
    const stream = tab === "chat-stream";
    return `from anthropic import Anthropic

client = Anthropic(api_key="${key}", base_url="${base}/v1")
res = client.messages.create(
    model="${model}",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],${stream ? "\n    stream=True," : ""}
)
${stream ? "for event in res:\n    print(event)" : "print(res.content[0].text)"}`;
  }
  if (protocol === "openai.responses") {
    const stream = tab === "chat-stream";
    return `from openai import OpenAI

client = OpenAI(api_key="${key}", base_url="${base}/v1")
res = client.responses.create(
    model="${model}",
    input="Hello!",${stream ? "\n    stream=True," : ""}
)
${stream ? "for event in res:\n    print(event)" : "print(res.output_text)"}`;
  }
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
  const [clientProtocol, setClientProtocol] = useState<ClientProtocol>("openai.chat");
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
  const activeModelRow = useMemo(() => (models || []).find((x) => x.id === activeModel), [models, activeModel]);
  const activeModality = useMemo(() => {
    return activeModelRow?.modality || "chat";
  }, [activeModelRow]);
  const activeProtocols = activeModality === "chat" ? routeProtocols(activeModelRow) : ["openai.chat" as ClientProtocol];
  const effectiveProtocol: ClientProtocol = activeProtocols.includes(clientProtocol) ? clientProtocol : activeProtocols[0];
  const activeKey = useMemo(() => {
    const k = (keys || []).find((x) => String(x.id) === keyId);
    if (k) return `${k.key_prefix}...`;
    return "sk-xxxxxxxx";
  }, [keys, keyId]);

  const filteredTabs = tabsForModality(activeModality);
  const effectiveTab: Tab = filteredTabs.includes(tab) ? tab : filteredTabs[0];
  const endpointPath = endpointForTab(effectiveTab, effectiveProtocol);

  const snippet = useMemo(() => {
    if (lang === "js") return buildJs(effectiveTab, effectiveProtocol, activeBase, activeKey, activeModel);
    if (lang === "py") return buildPy(effectiveTab, effectiveProtocol, activeBase, activeKey, activeModel);
    return buildCurl(effectiveTab, effectiveProtocol, activeBase, activeKey, activeModel);
  }, [lang, effectiveTab, effectiveProtocol, activeBase, activeKey, activeModel]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  }

  function selectModel(nextModel: Model) {
    setModel(nextModel.id);
    setTab(tabsForModality(nextModel.modality)[0]);
    setClientProtocol((nextModel.modality || "chat") === "chat" ? routeProtocols(nextModel)[0] : "openai.chat");
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
              const protocols = modality === "chat" ? routeProtocols(m) : ["openai.chat" as ClientProtocol];
              const modBadge =
                modality === "image"
                  ? "bg-purple-100 text-purple-700"
                  : modality === "embedding"
                  ? "bg-teal-100 text-teal-700"
                  : "bg-blue-100 text-blue-700";
              return (
                <button
                  key={m.id}
                  onClick={() => selectModel(m)}
                  className={`rounded border px-3 py-1.5 text-sm ${
                    (model || models[0].id) === m.id
                      ? "border-brand-600 bg-brand-50 text-brand-700"
                      : "border-gray-200 hover:bg-gray-50"
                  }`}
                  title={`${label}; client API ${m.modality || "chat"}; upstream adapter is automatic`}
                >
                  {m.id}
                  <span className={`ml-2 rounded px-1.5 py-0.5 text-xs ${modBadge}`}>{modality}</span>
                  {protocols.map((p) => (
                    <span key={p} className={`ml-2 rounded px-1.5 py-0.5 text-xs ${p === "anthropic.messages" ? "bg-purple-100 text-purple-700" : p === "openai.responses" ? "bg-blue-100 text-blue-700" : "bg-emerald-100 text-emerald-700"}`}>{protocolLabel(p)}</span>
                  ))}
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
          {activeModality === "chat" && (
            <>
              <span className="mr-2 text-gray-500">Protocol:</span>
              {activeProtocols.map((p) => (
                <button
                  key={p}
                  onClick={() => setClientProtocol(p)}
                  className={`rounded px-2 py-1 ${effectiveProtocol === p ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}
                >
                  {protocolLabel(p)}
                </button>
              ))}
            </>
          )}
          <span className="mr-2 text-gray-500">Client API:</span>
          {filteredTabs.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded px-2 py-1 ${effectiveTab === t ? "bg-brand-600 text-white" : "hover:bg-gray-100"}`}
            >
              {labelForTab(t)}
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
          Gateway: <code>{activeBase}</code> · Protocol: <code>{effectiveProtocol}</code> · Client endpoint: <code>{endpointPath}</code> · Model: <code>{activeModel}</code> · Upstream adapter: auto
          {activeGateway?.hint && (
            <span className="ml-1 text-gray-400">({activeGateway.hint})</span>
          )}
        </p>
      </div>
    </div>
  );
}
