import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import HeaderNav from "@/components/HeaderNav";

export const metadata: Metadata = {
  title: "llmxy — AI Token 中转站",
  description: "统一 Key，多模型，按量计费",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="border-b bg-white">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <Link href="/" className="text-xl font-bold text-brand-600">llmxy</Link>
            <HeaderNav />
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
        <footer className="mt-16 border-t bg-white py-6 text-center text-sm text-gray-500">
          © {new Date().getFullYear()} llmxy
        </footer>
      </body>
    </html>
  );
}
