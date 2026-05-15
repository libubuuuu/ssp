import type { Metadata } from "next";
import "./globals.css";
import JobPanel from "@/components/JobPanel";
import AuthFetchInterceptor from "@/components/AuthFetchInterceptor";
import CookieConsent from "@/components/CookieConsent";
import SiteFooter from "@/components/SiteFooter";
import WechatSupport from "@/components/WechatSupport";
import { LanguageProvider } from "@/lib/i18n/LanguageContext";

// 2026-05-12:腾讯云无法访问 fonts.gstatic.com,build 时 next/font/google 卡死。
// 去掉 Geist 字体下载,fallback 到 globals.css 里的 Arial/Helvetica/sans-serif(已配置)。
// 视觉差异极小(Geist 跟系统 sans-serif 视觉接近),换来 build 可工作。

export const metadata: Metadata = {
  title: "xiaoLi ai",
  description: "图片生成 | 视频生成",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>
        <LanguageProvider>
          <AuthFetchInterceptor />
          {children}
          <SiteFooter />
          <JobPanel />
          <CookieConsent />
          <WechatSupport />
        </LanguageProvider>
      </body>
    </html>
  );
}
