import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import HeaderNav from "@/components/HeaderNav";
import { ToastProvider } from "@/components/ui";

export const metadata: Metadata = {
  title: "llmxy — AI Token Gateway",
  description: "One key, many models, usage-based billing",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ToastProvider>
          <header className="border-b bg-white">
            <div className="mx-auto flex max-w-screen-2xl items-center justify-between px-6 py-4">
              <Link href="/" className="text-xl font-bold text-brand-600">
                llmxy
              </Link>
              <HeaderNav />
            </div>
          </header>
          <main className="px-6 py-8">{children}</main>
          <footer className="mt-16 border-t bg-white py-6 text-center text-sm text-gray-500">
            © {new Date().getFullYear()} llmxy
          </footer>
        </ToastProvider>
      </body>
    </html>
  );
}
