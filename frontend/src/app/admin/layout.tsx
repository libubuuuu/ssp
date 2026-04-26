"use client";
import AdminSidebar from "@/components/AdminSidebar";
import SystemHealthBanner from "@/components/SystemHealthBanner";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [authorized, setAuthorized] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const userStr = localStorage.getItem("user");
    if (!token || !userStr) {
      router.push("/auth");
      return;
    }
    try {
      const u = JSON.parse(userStr);
      if (u.role !== "admin") {
        router.push("/dashboard");
        return;
      }
      setAuthorized(true);
    } catch {
      router.push("/auth");
    } finally {
      setChecking(false);
    }
  }, [router]);

  // 视口检测:< 768px 视为移动端,默认收起侧栏
  useEffect(() => {
    if (typeof window === "undefined") return;
    const check = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      // 桌面端始终打开,移动端默认收起
      if (!mobile) setSidebarOpen(true);
    };
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  if (checking) {
    return (
      <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
        Loading...
      </div>
    );
  }

  if (!authorized) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#f5f3ed" }}>
      <AdminSidebar
        isOpen={isMobile ? sidebarOpen : true}
        onClose={() => setSidebarOpen(false)}
      />
      <main style={{ flex: 1, overflow: "auto", minWidth: 0 }}>
        {/* 移动端顶部 bar — 含汉堡按钮 */}
        {isMobile && (
          <div style={{
            position: "sticky",
            top: 0,
            zIndex: 50,
            background: "#fff",
            borderBottom: "1px solid #eee",
            padding: "0.6rem 1rem",
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
          }}>
            <button
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
              style={{
                background: "none",
                border: "1px solid #ddd",
                borderRadius: "6px",
                padding: "0.4rem 0.6rem",
                cursor: "pointer",
                fontSize: "1rem",
                lineHeight: 1,
              }}
            >
              ☰
            </button>
            <span style={{ fontSize: "0.95rem", fontWeight: 600, fontFamily: "Georgia,serif", fontStyle: "italic" }}>
              🛡️ AI Lixiao Admin
            </span>
          </div>
        )}
        <SystemHealthBanner />
        {children}
      </main>
    </div>
  );
}
