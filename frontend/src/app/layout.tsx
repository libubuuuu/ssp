import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import JobPanel from "@/components/JobPanel";
import AuthFetchInterceptor from "@/components/AuthFetchInterceptor";
import CookieConsent from "@/components/CookieConsent";
import { LanguageProvider } from "@/lib/i18n/LanguageContext";

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
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        <LanguageProvider>
          <AuthFetchInterceptor />
          {children}
          <JobPanel />
          <CookieConsent />
        </LanguageProvider>
      </body>
    </html>
  );
}
