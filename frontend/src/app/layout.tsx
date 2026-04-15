"use client";

import { useState, useEffect } from "react";
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
  title: "AI 创意平台",
  description: "图片生成 | 视频生成 | 数字人 | 语音克隆",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const [user, setUser] = useState<{ name: string; credits: number } | null>(null);

  useEffect(() => {
    const stored = localStorage.getItem("user");
    if (stored) {
      try {
        setUser(JSON.parse(stored));
      } catch {
        localStorage.removeItem("user");
      }
    }
  }, []);

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
          <Link href="/image" className="text-zinc-400 hover:text-white transition-colors">
            图片
          </Link>
          <Link href="/video" className="text-zinc-400 hover:text-white transition-colors">
            视频
          </Link>
          <Link href="/avatar" className="text-zinc-400 hover:text-white transition-colors">
            数字人
          </Link>
          <Link href="/voice-clone" className="text-zinc-400 hover:text-white transition-colors">
            语音
          </Link>
          {user ? (
            <>
              <Link href="/profile" className="text-zinc-400 hover:text-white transition-colors">
                {user.name} · {user.credits} 积分
              </Link>
            </>
          ) : (
            <Link href="/auth" className="px-4 py-2 rounded-lg bg-amber-500 text-black text-sm font-medium hover:bg-amber-400 transition-colors">
              登录
            </Link>
          )}
        </nav>
        <main className="flex-1">{children}</main>
      </body>
    </html>
  );
}
