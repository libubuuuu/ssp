"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface User {
  id: string;
  email: string;
  name: string | null;
  role: string;
  credits: number;
  created_at: string;
}

export default function AdminUsersPage() {
  const { lang } = useLang();
  const router = useRouter();
  const isEn = lang === "en";
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null); // user id 正在操作
  const [me, setMe] = useState<{ id: string } | null>(null);

  useEffect(() => {
    try {
      const u = localStorage.getItem("user");
      if (u) setMe(JSON.parse(u));
    } catch {}
  }, []);

  const load = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const res = await fetch(`${API_BASE}/api/admin/users-list`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 403) {
        alert(isEn ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      const data = await res.json();
      setUsers(data.users ?? []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const adjustCredits = async (user: User) => {
    const promptMsg = isEn
      ? `Adjust credits for ${user.email} (current: ${user.credits}). Enter delta (e.g. +50 or -10):`
      : `调整 ${user.email} 的积分(当前 ${user.credits})。输入 delta(如 +50 或 -10):`;
    const input = prompt(promptMsg);
    if (input === null) return;
    const delta = parseInt(input.trim(), 10);
    if (Number.isNaN(delta) || delta === 0) {
      alert(isEn ? "Invalid delta" : "无效数字");
      return;
    }
    setBusy(user.id);
    try {
      const token = localStorage.getItem("token") ?? "";
      const res = await fetch(`${API_BASE}/api/admin/users/${user.id}/adjust-credits?delta=${delta}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (res.ok) {
        alert(isEn
          ? `✅ Updated. ${user.email}: ${user.credits} → ${data.new_credits}`
          : `✅ 已更新。${user.email}:${user.credits} → ${data.new_credits}`);
        load();
      } else {
        alert(isEn ? `Failed: ${data.detail}` : `失败:${data.detail}`);
      }
    } finally {
      setBusy(null);
    }
  };

  const forceLogout = async (user: User) => {
    if (me && me.id === user.id) {
      const confirmSelf = confirm(isEn
        ? "You are about to force-logout YOURSELF. You'll be kicked back to login. Continue?"
        : "你在踢自己,会立刻回登录页,确定?");
      if (!confirmSelf) return;
    } else {
      const ok = confirm(isEn
        ? `Force-logout ${user.email}? Their tokens will be revoked across all devices.`
        : `强制踢出 ${user.email}?其所有设备的 token 立刻失效。`);
      if (!ok) return;
    }
    setBusy(user.id);
    try {
      const token = localStorage.getItem("token") ?? "";
      const res = await fetch(`${API_BASE}/api/admin/users/${user.id}/force-logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        alert(isEn ? "✅ Logged out" : "✅ 已踢出");
        if (me && me.id === user.id) {
          // 踢自己 → 拦截器会把当前页面跳登录,但保险起见手动跳
          localStorage.removeItem("token");
          localStorage.removeItem("refresh_token");
          localStorage.removeItem("user");
          router.push("/auth?expired=1");
          return;
        }
        load();
      } else {
        const data = await res.json();
        alert(isEn ? `Failed: ${data.detail}` : `失败:${data.detail}`);
      }
    } finally {
      setBusy(null);
    }
  };

  const fmtTime = (iso: string) => {
    try { return new Date(iso).toLocaleString(isEn ? "en-US" : "zh-CN"); }
    catch { return iso; }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
            {isEn ? "User Management" : "用户管理"}
          </h1>
          <button onClick={load} style={{ background: "none", border: "1px solid #ddd", padding: "0.5rem 1rem", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            ⟳ {isEn ? "Refresh" : "刷新"}
          </button>
        </div>

        <div style={{ background: "#fff", borderRadius: "10px", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
          {loading ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>{isEn ? "Loading..." : "加载中..."}</div>
          ) : users.length === 0 ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>{isEn ? "No users" : "暂无用户"}</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88rem" }}>
              <thead>
                <tr style={{ background: "#fafafa", borderBottom: "1px solid #eee" }}>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Email" : "邮箱"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Name" : "昵称"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Role" : "角色"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "right", fontWeight: 500, color: "#666" }}>{isEn ? "Credits" : "积分"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Joined" : "注册时间"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "right", fontWeight: 500, color: "#666" }}>{isEn ? "Actions" : "操作"}</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => {
                  const isMe = me && me.id === u.id;
                  return (
                    <tr key={u.id} style={{ borderBottom: "1px solid #f4f4f4", background: isMe ? "#fffbe8" : "transparent" }}>
                      <td style={{ padding: "0.75rem 1rem", color: "#0d0d0d" }}>
                        {u.email}{isMe && <span style={{ marginLeft: "0.5rem", fontSize: "0.7rem", color: "#a87a1a", fontWeight: 600 }}>{isEn ? "(you)" : "(你)"}</span>}
                      </td>
                      <td style={{ padding: "0.75rem 1rem", color: "#666" }}>{u.name ?? "—"}</td>
                      <td style={{ padding: "0.75rem 1rem" }}>
                        <span style={{ display: "inline-block", padding: "0.15rem 0.5rem", borderRadius: "4px", background: u.role === "admin" ? "#1a1a1a" : "#eee", color: u.role === "admin" ? "#fff" : "#666", fontSize: "0.75rem", fontWeight: 500 }}>
                          {u.role}
                        </span>
                      </td>
                      <td style={{ padding: "0.75rem 1rem", color: "#0d0d0d", textAlign: "right", fontFamily: "monospace" }}>{u.credits}</td>
                      <td style={{ padding: "0.75rem 1rem", color: "#888", fontSize: "0.8rem", whiteSpace: "nowrap" }}>{fmtTime(u.created_at)}</td>
                      <td style={{ padding: "0.75rem 1rem", textAlign: "right", whiteSpace: "nowrap" }}>
                        <button
                          disabled={busy === u.id}
                          onClick={() => adjustCredits(u)}
                          style={{ marginRight: "0.5rem", padding: "0.35rem 0.75rem", border: "1px solid #ddd", background: "#fff", borderRadius: "6px", cursor: busy === u.id ? "default" : "pointer", fontSize: "0.78rem", opacity: busy === u.id ? 0.5 : 1 }}
                        >
                          {isEn ? "± Credits" : "± 积分"}
                        </button>
                        <button
                          disabled={busy === u.id}
                          onClick={() => forceLogout(u)}
                          style={{ padding: "0.35rem 0.75rem", border: "1px solid #c33", background: "#fff", color: "#c33", borderRadius: "6px", cursor: busy === u.id ? "default" : "pointer", fontSize: "0.78rem", opacity: busy === u.id ? 0.5 : 1 }}
                        >
                          {isEn ? "Force logout" : "踢出"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <p style={{ marginTop: "1rem", fontSize: "0.8rem", color: "#999" }}>
          {isEn
            ? `${users.length} user${users.length !== 1 ? "s" : ""}. Actions are logged to audit log.`
            : `共 ${users.length} 个用户。所有操作会写入审计日志。`}
        </p>
      </div>
    </div>
  );
}
