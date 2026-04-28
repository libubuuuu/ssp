"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface AuditLog {
  id: string;
  actor_user_id: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown> | null;
  ip: string | null;
  created_at: string;
}

export default function AdminAuditPage() {
  const { lang } = useLang();
  const router = useRouter();
  const isEn = lang === "en";
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionFilter, setActionFilter] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const qs = actionFilter ? `?action=${encodeURIComponent(actionFilter)}` : "";
      const res = await fetch(`${API_BASE}/api/admin/audit-log${qs}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 403) {
        alert(isEn ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      const data = await res.json();
      setLogs(data.logs ?? []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [actionFilter, isEn, router]);

  useEffect(() => { load(); }, [load]);

  const formatTime = (iso: string) => {
    try { return new Date(iso).toLocaleString(isEn ? "en-US" : "zh-CN"); }
    catch { return iso; }
  };

  const formatDetails = (d: Record<string, unknown> | null) => {
    if (!d) return "—";
    try { return JSON.stringify(d, null, 0); }
    catch { return String(d); }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
            {isEn ? "Audit Log" : "审计日志"}
          </h1>
          <button onClick={() => router.push("/admin/settings")} style={{ background: "none", border: "1px solid #ddd", padding: "0.5rem 1rem", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            ← {isEn ? "Back" : "返回"}
          </button>
        </div>

        {/* 过滤栏 */}
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          {[
            { v: "", zh: "全部", en: "All" },
            { v: "adjust_credits", zh: "改额度", en: "Adjust Credits" },
            { v: "confirm_order", zh: "确认订单", en: "Confirm Order" },
            { v: "force_logout", zh: "强制下线", en: "Force Logout" },
            { v: "change_password", zh: "改密码", en: "Change Password" },
            { v: "reset_password", zh: "重置密码", en: "Reset Password" },
            { v: "logout_all_devices", zh: "登出所有设备", en: "Logout All" },
            { v: "reset_model", zh: "重置模型", en: "Reset Model" },
          ].map(opt => (
            <button
              key={opt.v}
              onClick={() => setActionFilter(opt.v)}
              style={{
                padding: "0.4rem 0.9rem",
                border: actionFilter === opt.v ? "1px solid #0d0d0d" : "1px solid #ddd",
                background: actionFilter === opt.v ? "#0d0d0d" : "#fff",
                color: actionFilter === opt.v ? "#fff" : "#666",
                borderRadius: "6px",
                cursor: "pointer",
                fontSize: "0.85rem",
              }}
            >
              {isEn ? opt.en : opt.zh}
            </button>
          ))}
          <button onClick={load} style={{ marginLeft: "auto", padding: "0.4rem 0.9rem", border: "1px solid #ddd", background: "#fff", borderRadius: "6px", cursor: "pointer", fontSize: "0.85rem" }}>
            ⟳ {isEn ? "Refresh" : "刷新"}
          </button>
        </div>

        {/* 表格 */}
        <div style={{ background: "#fff", borderRadius: "10px", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
          {loading ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>{isEn ? "Loading..." : "加载中..."}</div>
          ) : logs.length === 0 ? (
            <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>{isEn ? "No records" : "暂无记录"}</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
              <thead>
                <tr style={{ background: "#fafafa", borderBottom: "1px solid #eee" }}>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Time" : "时间"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Admin" : "管理员"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Action" : "动作"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Target" : "目标"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>{isEn ? "Details" : "详情"}</th>
                  <th style={{ padding: "0.75rem 1rem", textAlign: "left", fontWeight: 500, color: "#666" }}>IP</th>
                </tr>
              </thead>
              <tbody>
                {logs.map(log => (
                  <tr key={log.id} style={{ borderBottom: "1px solid #f4f4f4" }}>
                    <td style={{ padding: "0.75rem 1rem", color: "#0d0d0d", whiteSpace: "nowrap" }}>{formatTime(log.created_at)}</td>
                    <td style={{ padding: "0.75rem 1rem", color: "#333" }}>{log.actor_email ?? log.actor_user_id.slice(0, 8)}</td>
                    <td style={{ padding: "0.75rem 1rem" }}>
                      <span style={{ display: "inline-block", padding: "0.15rem 0.5rem", borderRadius: "4px", background: "#f0e8d8", color: "#7a5b1f", fontSize: "0.78rem", fontWeight: 500 }}>
                        {log.action}
                      </span>
                    </td>
                    <td style={{ padding: "0.75rem 1rem", color: "#666", fontFamily: "monospace", fontSize: "0.8rem" }}>
                      {log.target_type && log.target_id ? `${log.target_type}: ${log.target_id.slice(0, 8)}…` : "—"}
                    </td>
                    <td style={{ padding: "0.75rem 1rem", color: "#666", fontFamily: "monospace", fontSize: "0.78rem", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {formatDetails(log.details)}
                    </td>
                    <td style={{ padding: "0.75rem 1rem", color: "#999", fontFamily: "monospace", fontSize: "0.8rem" }}>{log.ip ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <p style={{ marginTop: "1rem", fontSize: "0.8rem", color: "#999" }}>
          {isEn
            ? `${logs.length} record${logs.length !== 1 ? "s" : ""} (max 100, oldest may be truncated)`
            : `共 ${logs.length} 条记录(最多 100 条,旧记录可能被截断)`}
        </p>
      </div>
    </div>
  );
}
