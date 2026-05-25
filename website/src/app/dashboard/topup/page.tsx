"use client";
import { useState } from "react";
import { api } from "@/lib/api";

const CHANNELS = [
  { id: "alipay", label: "支付宝" },
  { id: "wechat", label: "微信支付" },
  { id: "stripe", label: "Stripe" },
];

export default function TopupPage() {
  const [amount, setAmount] = useState(100);
  const [channel, setChannel] = useState("alipay");
  const [resp, setResp] = useState<any>(null);
  const [err, setErr] = useState("");

  async function pay() {
    setErr("");
    try {
      const r = await api("/api/v1/orders", {
        method: "POST",
        body: JSON.stringify({ amount_cents: Math.round(amount * 100), channel }),
      });
      setResp(r);
    } catch (e: any) { setErr(e.message); }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">充值</h1>
      <div className="card space-y-4">
        <div>
          <label className="label">金额 (元)</label>
          <input type="number" min={1} className="input" value={amount} onChange={(e) => setAmount(Number(e.target.value))} />
        </div>
        <div>
          <label className="label">支付方式</label>
          <div className="flex gap-3">
            {CHANNELS.map((c) => (
              <label key={c.id} className="flex items-center gap-2">
                <input type="radio" name="ch" checked={channel === c.id} onChange={() => setChannel(c.id)} />
                {c.label}
              </label>
            ))}
          </div>
        </div>
        <button className="btn-primary" onClick={pay}>下单</button>
        {err && <p className="text-sm text-red-600">{err}</p>}
        {resp && (
          <div className="rounded border border-gray-200 bg-gray-50 p-4 text-sm">
            <p>订单号：#{resp.order_id}</p>
            {resp.pay_url && <p>支付链接：<a className="text-brand-600 underline" href={resp.pay_url} target="_blank">点此支付</a></p>}
            {resp.qr_code && <p>扫码：<code>{resp.qr_code}</code></p>}
            <p className="mt-2 text-xs text-gray-500">（开发环境为 stub，访问 mock-pay 即可模拟支付成功）</p>
          </div>
        )}
      </div>
    </div>
  );
}
