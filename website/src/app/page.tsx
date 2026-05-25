import Link from "next/link";

export default function Home() {
  return (
    <div className="space-y-12 py-8">
      <section className="text-center">
        <h1 className="text-5xl font-bold">一个 Key，调用所有大模型</h1>
        <p className="mt-4 text-lg text-gray-600">
          兼容 OpenAI SDK，智能路由 GPT、Claude、国内 LLM，按 Token 计费，余额可控。
        </p>
        <div className="mt-8 flex justify-center gap-4">
          <Link href="/register" className="btn-primary">免费开始</Link>
          <Link href="/pricing" className="btn-outline">查看套餐</Link>
        </div>
      </section>
      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <Feature title="统一接口" desc="完全兼容 OpenAI /v1/chat/completions，零修改迁移代码。" />
        <Feature title="智能路由" desc="原生支持 OpenAI / Claude / Gemini，按权重路由 + 自动 fallback。" />
        <Feature title="按量计费" desc="Token 级精细计费，多档套餐，余额随时查询。" />
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
