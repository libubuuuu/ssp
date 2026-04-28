"use client";
import AdminSidebar from "@/components/AdminSidebar";
import SystemHealthBanner from "@/components/SystemHealthBanner";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";
import { useIsMobile } from "@/lib/hooks/useIsMobile";

interface AdminUserCache {
  id?: string;
  email?: string;
  role?: string;
  totp_enabled?: boolean;
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const token = useLocalStorageItem("token");
  const userJson = useLocalStorageItem("user");
  const user: AdminUserCache | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as AdminUserCache; } catch { return null; }
  }, [userJson]);
  const isMobile = useIsMobile();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // 桌面端 sidebar 默认开,移动端默认收
  useEffect(() => {
    if (!isMobile) setSidebarOpen(true);
  }, [isMobile]);

  // 未登录 / 非管理员 → 跳走(放在 effect 因为 router.push 不允许在 render)
  useEffect(() => {
    if (!token || !user) {
      router.push("/auth");
      return;
    }
    if (user.role !== "admin") {
      router.push("/dashboard");
    }
  }, [token, user, router]);

  // 还在 hydration / 校验中
  if (!token || !user || user.role !== "admin") {
    return <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>Loading...</div>;
  }

  const needs2FA = user.totp_enabled === false;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#f5f3ed" }}>
      <AdminSidebar
        isOpen={isMobile ? sidebarOpen : true}
        onClose={() => setSidebarOpen(false)}
      />
      <main style={{ flex: 1, overflow: "auto", minWidth: 0 }}>
        {isMobile && (
          <div style={{
            position: "sticky", top: 0, zIndex: 50, background: "#fff",
            borderBottom: "1px solid #eee", padding: "0.6rem 1rem",
            display: "flex", alignItems: "center", gap: "0.75rem",
          }}>
            <button
              onClick={() => setSidebarOpen(true)}
              aria-label="Open menu"
              style={{
                background: "none", border: "1px solid #ddd", borderRadius: "6px",
                padding: "0.4rem 0.6rem", cursor: "pointer", fontSize: "1rem", lineHeight: 1,
              }}
            >☰</button>
            <span style={{ fontSize: "0.95rem", fontWeight: 600, fontFamily: "Georgia,serif", fontStyle: "italic" }}>
              🛡️ AI Lixiao Admin
            </span>
          </div>
        )}
        {/* 管理员未启用 2FA 时引导启用 — 当后端 ADMIN_2FA_REQUIRED=true 时这里就是硬墙 */}
        {needs2FA && (
          <div style={{
            background: "#fff8e6", borderBottom: "2px solid #f59e0b",
            padding: "0.85rem 1.25rem", display: "flex", alignItems: "center",
            gap: "0.85rem", fontSize: "0.9rem",
          }}>
            <span style={{ fontSize: "1.2rem" }}>🛡️</span>
            <div style={{ flex: 1, color: "#7a4f00" }}>
              <strong>建议立即启用 2FA</strong> — 管理员账号是高价值目标,启用 TOTP 后即使密码泄漏也安全。
              <span style={{ color: "#a06900", marginLeft: "0.5rem" }}>
                (未来 ADMIN_2FA_REQUIRED 启用时,本账号将无法访问后台直到 enroll)
              </span>
            </div>
            <Link
              href="/profile/2fa"
              style={{
                background: "#f59e0b", color: "#fff", padding: "0.5rem 1rem",
                borderRadius: "6px", textDecoration: "none", fontWeight: 500,
                whiteSpace: "nowrap",
              }}
            >
              去启用 →
            </Link>
          </div>
        )}
        <SystemHealthBanner />
        {children}
      </main>
    </div>
  );
}
