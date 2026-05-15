"use client";
import { useState } from "react";
import { usePathname } from "next/navigation";

export default function WechatSupport() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // 管理后台不显示客服按钮
  if (pathname?.startsWith("/admin")) return null;

  return (
    <>
      {/* 悬浮客服按钮 */}
      <button
        onClick={() => setOpen(true)}
        aria-label="微信客服"
        title="微信客服"
        style={{
          position: "fixed",
          bottom: "28px",
          right: "24px",
          width: "52px",
          height: "52px",
          borderRadius: "50%",
          background: "#07C160",
          border: "none",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "0 4px 14px rgba(7,193,96,0.45)",
          zIndex: 9000,
          transition: "transform 0.15s, box-shadow 0.15s",
        }}
        onMouseEnter={e => {
          (e.currentTarget as HTMLButtonElement).style.transform = "scale(1.08)";
          (e.currentTarget as HTMLButtonElement).style.boxShadow = "0 6px 20px rgba(7,193,96,0.55)";
        }}
        onMouseLeave={e => {
          (e.currentTarget as HTMLButtonElement).style.transform = "scale(1)";
          (e.currentTarget as HTMLButtonElement).style.boxShadow = "0 4px 14px rgba(7,193,96,0.45)";
        }}
      >
        {/* 微信图标 */}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg">
          <path d="M8.5 2C4.36 2 1 5.02 1 8.75c0 1.95.97 3.7 2.5 4.9a.5.5 0 0 1 .18.56l-.33 1.26c-.02.06-.04.12-.04.18 0 .14.11.25.25.25a.28.28 0 0 0 .14-.04l1.62-.95a.73.73 0 0 1 .61-.08c.8.22 1.65.34 2.57.34-.1-.41-.15-.83-.15-1.27C8.35 10.55 12 7.6 16.5 7.6c.23 0 .45.01.67.03C16.17 4.42 12.7 2 8.5 2zM5.75 7.5a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5zm5 0a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5z"/>
          <path d="M23 14.25c0-2.9-2.8-5.25-6.25-5.25S10.5 11.35 10.5 14.25 13.3 19.5 16.75 19.5c.73 0 1.43-.1 2.08-.29a.65.65 0 0 1 .53.07l1.33.78a.24.24 0 0 0 .12.04c.11 0 .19-.09.19-.2a.37.37 0 0 0-.03-.14l-.27-1.03a.41.41 0 0 1 .15-.46C22.1 17.38 23 15.9 23 14.25zm-8.25-1a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5zm4 0a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5z"/>
        </svg>
      </button>

      {/* 弹窗蒙层 */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.48)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 10000,
          }}
        >
          {/* 弹窗内容 */}
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: "#fff",
              borderRadius: "18px",
              padding: "36px 32px 28px",
              textAlign: "center",
              boxShadow: "0 24px 64px rgba(0,0,0,0.18)",
              maxWidth: "320px",
              width: "90%",
              position: "relative",
            }}
          >
            {/* 关闭按钮 */}
            <button
              onClick={() => setOpen(false)}
              aria-label="关闭"
              style={{
                position: "absolute",
                top: "14px",
                right: "18px",
                background: "none",
                border: "none",
                fontSize: "22px",
                cursor: "pointer",
                color: "#bbb",
                lineHeight: 1,
                padding: 0,
              }}
            >
              ×
            </button>

            <p style={{ fontSize: "1.05rem", fontWeight: 600, marginBottom: "20px", color: "#111" }}>
              扫码添加微信客服
            </p>

            <img
              src="/images/wechat-qr.jpg"
              alt="微信客服二维码"
              style={{
                width: "200px",
                height: "200px",
                objectFit: "cover",
                borderRadius: "10px",
                border: "1px solid #eee",
              }}
            />

            <p style={{ marginTop: "14px", fontSize: "0.84rem", color: "#888", lineHeight: 1.5 }}>
              工作时间内会尽快回复
            </p>
          </div>
        </div>
      )}
    </>
  );
}
