import Link from "next/link";
import HeroCta from "@/components/HeroCta";

export default function Home() {
  return (
    <div className="space-y-12 py-8">
      <section className="text-center">
        <h1 className="text-5xl font-bold">One key, every LLM</h1>
        <p className="mt-4 text-lg text-gray-600">
          OpenAI SDK compatible. Smart routing across GPT, Claude, and open-source LLMs. Token-level billing with full balance control.
        </p>
        <div className="mt-8 flex justify-center gap-4">
          <HeroCta />
          <Link href="/pricing" className="btn-outline">View plans</Link>
        </div>
      </section>
      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <Feature title="Unified API" desc="Fully compatible with OpenAI /v1/chat/completions. Migrate without code changes." />
        <Feature title="Smart routing" desc="Native support for OpenAI / Claude / Gemini, weighted routing with automatic fallback." />
        <Feature title="Usage-based billing" desc="Fine-grained token-level billing, tiered plans, real-time balance lookups." />
      </section>
    </div>
  );
}

function Feature({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="card">
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="mt-2 text-sm text-gray-600">{desc}</p>
    </div>
  );
}
