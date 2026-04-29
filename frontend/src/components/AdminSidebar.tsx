"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useRouter, usePathname } from "next/navigation";
import { useMemo } from "react";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";
import { useIsMobile } from "@/lib/hooks/useIsMobile";
import { clearAuthSession } from "@/lib/userState";

interface AdminUser {
  id?: string | number;
  name?: string;
  email?: string;
  role?: string;
}

interface Props {
  isOpen?: boolean;
  onClose?: () => void;
}

export default function AdminSidebar({ isOpen = true, onClose }: Props) {
  const { lang } = useLang();
  const router = useRouter();
  const pathname = usePathname();
  const userJson = useLocalStorageItem("user");
  const user: AdminUser | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as AdminUser; } catch { return null; }
  }, [userJson]);
  const isMobile = useIsMobile();
  const isEn = lang === "en";

  const logout = () => {
    clearAuthSession();
    router.push("/auth");
  };

  const menuItems = [
    { path: "/admin/users", icon: "👥", zh: "用户管理", en: "Users" },
    { path: "/admin/orders", icon: "💰", zh: "订单管理", en: "Orders" },
    { path: "/admin/dashboard", icon: "📊", zh: "系统监控", en: "Monitor" },
    { path: "/admin/oral", icon: "🎤", zh: "口播任务", en: "Oral Tasks" },
    { path: "/admin/audit", icon: "📜", zh: "审计日志", en: "Audit Log" },
    { path: "/admin/diagnose", icon: "🩺", zh: "诊断历史", en: "Diagnose" },
    { path: "/admin/settings", icon: "⚙", zh: "管理员设置", en: "Settings" },
  ];

  if (!user) return null;

  // 移动端点菜单后自动关闭侧栏
  const handleNav = (path: string) => {
    router.push(path);
    if (isMobile && onClose) onClose();
  };

  // 移动端隐藏:transform translate 把 aside 移出屏幕
  const mobileHidden = isMobile && !isOpen;

  return (
    <>
      {/* 移动端蒙层(打开时点击关闭) */}
      {isMobile && isOpen && (
        <div
          onClick={onClose}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.5)",
            zIndex: 99,
          }}
        />
      )}

      <aside style={{
        width: "240px",
        background: "#1a1a1a",
        color: "#fff",
        padding: "1.5rem 0",
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        // 桌面端 sticky,移动端 fixed(从屏幕左侧滑出)
        position: isMobile ? "fixed" : "sticky",
        top: 0,
        left: 0,
        flexShrink: 0,
        zIndex: 100,
        transform: mobileHidden ? "translateX(-100%)" : "translateX(0)",
        transition: "transform 0.25s ease-out",
        boxShadow: isMobile && isOpen ? "2px 0 16px rgba(0,0,0,0.4)" : "none",
      }}>
        {/* Logo + 移动端关闭按钮 */}
        <div style={{ padding: "0 1.5rem 1.5rem", borderBottom: "1px solid #333", display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: "1.3rem", fontWeight: 700, fontFamily: "Georgia,serif", fontStyle: "italic" }}>
              🛡️ AI Lixiao
            </div>
            <div style={{ fontSize: "0.75rem", color: "#888", marginTop: "0.25rem" }}>
              {isEn ? "Admin Portal" : "管理后台"}
            </div>
          </div>
          {isMobile && (
            <button
              onClick={onClose}
              aria-label="Close menu"
              style={{ background: "none", border: "none", color: "#888", fontSize: "1.5rem", cursor: "pointer", padding: 0, lineHeight: 1 }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Menu */}
        <nav style={{ flex: 1, padding: "1rem 0.75rem" }}>
          {menuItems.map(item => {
            const active = pathname === item.path;
            return (
              <button
                key={item.path}
                onClick={() => handleNav(item.path)}
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  gap: "0.75rem",
                  padding: "0.75rem 1rem",
                  marginBottom: "0.25rem",
                  background: active ? "#2d2d2d" : "transparent",
                  border: "none",
                  borderLeft: active ? "3px solid #f59e0b" : "3px solid transparent",
                  color: active ? "#fff" : "#aaa",
                  cursor: "pointer",
                  fontSize: "0.9rem",
                  textAlign: "left",
                  borderRadius: "6px",
                  transition: "all 0.15s",
                }}
                onMouseEnter={e => { if (!active) e.currentTarget.style.background = "#252525"; }}
                onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ fontSize: "1.1rem" }}>{item.icon}</span>
                {isEn ? item.en : item.zh}
              </button>
            );
          })}
        </nav>

        {/* User */}
        <div style={{ padding: "1rem 1.5rem", borderTop: "1px solid #333" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.75rem" }}>
            <div style={{
              width: "36px",
              height: "36px",
              borderRadius: "50%",
              background: "#f59e0b",
              color: "#000",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 600,
            }}>
              {(user.name || user.email || "?").charAt(0).toUpperCase()}
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
              <div style={{ fontSize: "0.85rem", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {user.name || user.email}
              </div>
              <div style={{ fontSize: "0.7rem", color: "#888" }}>
                {user.role === "admin" ? (isEn ? "Administrator" : "管理员") : user.role}
              </div>
            </div>
          </div>
          <button onClick={logout} style={{
            width: "100%",
            padding: "0.5rem",
            background: "transparent",
            border: "1px solid #333",
            color: "#c66",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "0.8rem",
          }}>
            🚪 {isEn ? "Log Out" : "退出登录"}
          </button>
        </div>
      </aside>
    </>
  );
}
