"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export default function AdminSidebar() {
  const { lang } = useLang();
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<any>(null);
  const isEn = lang === "en";

  useEffect(() => {
    const u = localStorage.getItem("user");
    if (u) try { setUser(JSON.parse(u)); } catch {}
  }, []);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    router.push("/auth");
  };

  const menuItems = [
    { path: "/admin/orders", icon: "💰", zh: "订单管理", en: "Orders" },
    { path: "/admin/dashboard", icon: "📊", zh: "系统监控", en: "Monitor" },
    { path: "/admin/audit", icon: "📜", zh: "审计日志", en: "Audit Log" },
    { path: "/admin/settings", icon: "⚙", zh: "管理员设置", en: "Settings" },
  ];

  if (!user) return null;

  return (
    <aside style={{
      width: "240px",
      background: "#1a1a1a",
      color: "#fff",
      padding: "1.5rem 0",
      display: "flex",
      flexDirection: "column",
      height: "100vh",
      position: "sticky",
      top: 0,
      flexShrink: 0,
    }}>
      {/* Logo */}
      <div style={{ padding: "0 1.5rem 1.5rem", borderBottom: "1px solid #333" }}>
        <div style={{ fontSize: "1.3rem", fontWeight: 700, fontFamily: "Georgia,serif", fontStyle: "italic" }}>
          🛡️ AI Lixiao
        </div>
        <div style={{ fontSize: "0.75rem", color: "#888", marginTop: "0.25rem" }}>
          {isEn ? "Admin Portal" : "管理后台"}
        </div>
      </div>

      {/* Menu */}
      <nav style={{ flex: 1, padding: "1rem 0.75rem" }}>
        {menuItems.map(item => {
          const active = pathname === item.path;
          return (
            <button
              key={item.path}
              onClick={() => router.push(item.path)}
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
  );
}
