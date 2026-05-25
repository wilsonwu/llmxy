"use client";
import { useState } from "react";
import { api } from "@/lib/api";

const CHANNELS = [
  { id: "alipay", label: "Alipay" },
  { id: "wechat", label: "WeChat Pay" },
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
      <h1 className="text-2xl font-bold">Top up</h1>
      <div className="card space-y-4">
        <div>
          <label className="label">Amount</label>
          <input type="number" min={1} className="input" value={amount} onChange={(e) => setAmount(Number(e.target.value))} />
        </div>
        <div>
          <label className="label">Payment method</label>
          <div className="flex gap-3">
            {CHANNELS.map((c) => (
              <label key={c.id} className="flex items-center gap-2">
                <input type="radio" name="ch" checked={channel === c.id} onChange={() => setChannel(c.id)} />
                {c.label}
              </label>
            ))}
          </div>
        </div>
        <button className="btn-primary" onClick={pay}>Place order</button>
        {err && <p className="text-sm text-red-600">{err}</p>}
        {resp && (
          <div className="rounded border border-gray-200 bg-gray-50 p-4 text-sm">
            <p>Order ID: #{resp.order_id}</p>
            {resp.pay_url && <p>Pay link: <a className="text-brand-600 underline" href={resp.pay_url} target="_blank">Pay now</a></p>}
            {resp.qr_code && <p>QR code: <code>{resp.qr_code}</code></p>}
            <p className="mt-2 text-xs text-gray-500">(Dev stub — open /mock-pay to simulate a successful payment)</p>
          </div>
        )}
      </div>
    </div>
  );
}
