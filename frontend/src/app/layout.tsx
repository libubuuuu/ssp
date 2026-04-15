import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import Link from "next/link";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI 创意平台 - 3D 穿衣设计定制",
  description: "3D 试衣 | 服装设计 | 在线定制",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-950 text-zinc-100">
        <nav className="border-b border-zinc-800 px-6 py-4 flex gap-6 items-center">
          <Link href="/" className="font-semibold text-lg hover:text-amber-400 transition-colors">
            AI 创意平台
          </Link>
          <div className="flex-1" />
          <Link href="/canvas" className="text-zinc-400 hover:text-white transition-colors">
            3D 画布
          </Link>
          <Link href="/try-on" className="text-zinc-400 hover:text-white transition-colors">
            3D 试穿
          </Link>
          <Link href="/products" className="text-zinc-400 hover:text-white transition-colors">
            服装商城
          </Link>
          <Link href="/merchant" className="px-4 py-2 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 transition-colors">
            商家后台
          </Link>
        </nav>
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
