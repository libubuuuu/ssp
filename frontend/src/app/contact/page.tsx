import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "联系客服 | xiaoLi ai",
  description: "扫码添加微信客服，工作时间内会尽快回复",
};

export default function ContactPage() {
  return (
    <div style={{
      minHeight: "100vh",
      background: "#fafaf8",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: "2rem",
    }}>
      <div style={{
        background: "#fff",
        borderRadius: "20px",
        padding: "52px 40px 40px",
        textAlign: "center",
        boxShadow: "0 4px 28px rgba(0,0,0,0.07)",
        maxWidth: "420px",
        width: "100%",
      }}>
        {/* 微信图标 */}
        <div style={{
          width: "56px",
          height: "56px",
          borderRadius: "50%",
          background: "#07C160",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          margin: "0 auto 20px",
        }}>
          <svg width="30" height="30" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
            <path d="M8.5 2C4.36 2 1 5.02 1 8.75c0 1.95.97 3.7 2.5 4.9a.5.5 0 0 1 .18.56l-.33 1.26c-.02.06-.04.12-.04.18 0 .14.11.25.25.25a.28.28 0 0 0 .14-.04l1.62-.95a.73.73 0 0 1 .61-.08c.8.22 1.65.34 2.57.34-.1-.41-.15-.83-.15-1.27C8.35 10.55 12 7.6 16.5 7.6c.23 0 .45.01.67.03C16.17 4.42 12.7 2 8.5 2zM5.75 7.5a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5zm5 0a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5z"/>
            <path d="M23 14.25c0-2.9-2.8-5.25-6.25-5.25S10.5 11.35 10.5 14.25 13.3 19.5 16.75 19.5c.73 0 1.43-.1 2.08-.29a.65.65 0 0 1 .53.07l1.33.78a.24.24 0 0 0 .12.04c.11 0 .19-.09.19-.2a.37.37 0 0 0-.03-.14l-.27-1.03a.41.41 0 0 1 .15-.46C22.1 17.38 23 15.9 23 14.25zm-8.25-1a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5zm4 0a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5z"/>
          </svg>
        </div>

        <h1 style={{ fontSize: "1.5rem", fontWeight: 700, marginBottom: "10px", color: "#111" }}>
          联系客服
        </h1>
        <p style={{ color: "#888", fontSize: "0.9rem", marginBottom: "32px", lineHeight: 1.6 }}>
          扫码添加微信客服<br />
          工作时间内会尽快回复
        </p>

        <img
          src="/images/wechat-qr.jpg"
          alt="微信客服二维码"
          style={{
            width: "220px",
            height: "220px",
            objectFit: "cover",
            borderRadius: "12px",
            border: "1px solid #eee",
            boxShadow: "0 2px 12px rgba(0,0,0,0.08)",
          }}
        />

        <p style={{
          marginTop: "20px",
          fontSize: "0.82rem",
          color: "#bbb",
        }}>
          如无法扫码，请直接搜索微信号添加
        </p>

        <div style={{ marginTop: "32px", borderTop: "1px solid #f0f0f0", paddingTop: "24px" }}>
          <Link
            href="/"
            style={{
              color: "#07C160",
              textDecoration: "none",
              fontSize: "0.9rem",
              fontWeight: 500,
            }}
          >
            ← 返回首页
          </Link>
        </div>
      </div>
    </div>
  );
}
