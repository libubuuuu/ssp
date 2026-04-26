"use client";
/**
 * 系统健康 Banner — 挂在 admin/layout 顶部
 * - 每 30 秒拉一次 /api/admin/watchdog
 * - overall == "ok" 且最近 1h 无告警 → 不渲染(不打扰)
 * - 有 WARN/CRIT → 顶部红色/黄色 banner,显示告警条数 + 点击展开详情
 * - 后台 tab 不 poll(visibilitychange)
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface WatchdogData {
  overall: "ok" | "warn" | "critical" | "unknown";
  last_run: string | null;
  recent_alerts_1h: number;
  log_tail: string[];
  alerts_tail: string[];
}

export default function SystemHealthBanner() {
  const router = useRouter();
  const [data, setData] = useState<WatchdogData | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (typeof document === "undefined") return;

    const load = async () => {
      if (document.hidden) return;
      try {
        const token = localStorage.getItem("token") ?? "";
        if (!token) return;
        const r = await fetch(`${API_BASE}/api/admin/watchdog`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (r.ok) {
          const d: WatchdogData = await r.json();
          setData(d);
        }
      } catch {}
    };

    load();
    const interval = setInterval(load, 30000); // 每 30 秒

    const onVis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  // 健康 + 最近 1h 无告警 → 完全不显示
  if (!data) return null;
  if (data.overall === "ok" && data.recent_alerts_1h === 0) return null;

  const isCrit = data.overall === "critical";
  const isWarn = data.overall === "warn" || data.recent_alerts_1h > 0;

  const bg = isCrit ? "#3a1a1a" : isWarn ? "#3a2a1a" : "#2a2a2a";
  const border = isCrit ? "#c33" : isWarn ? "#c80" : "#666";
  const fg = isCrit ? "#ff8a8a" : isWarn ? "#fbbf24" : "#aaa";
  const icon = isCrit ? "🚨" : isWarn ? "⚠️" : "•";
  const label = isCrit ? "生产异常" : isWarn ? "监控告警" : "未知状态";

  return (
    <div style={{
      background: bg,
      borderBottom: `1px solid ${border}`,
      color: fg,
      padding: "0.6rem 1rem",
      fontSize: "0.85rem",
      cursor: "pointer",
    }}
    onClick={() => setExpanded(!expanded)}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
        <span style={{ fontSize: "1rem" }}>{icon}</span>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span>·</span>
        <span>最近 1 小时告警 <strong>{data.recent_alerts_1h}</strong> 条</span>
        <span>·</span>
        <span style={{ fontSize: "0.78rem", opacity: 0.7 }}>最后检查 {data.last_run ?? "—"}</span>
        <span style={{ marginLeft: "auto", fontSize: "0.78rem", opacity: 0.7 }}>
          {expanded ? "收起 ▲" : "展开详情 ▼"}
        </span>
      </div>

      {expanded && (
        <div style={{ marginTop: "0.75rem", padding: "0.75rem", background: "rgba(0,0,0,0.3)", borderRadius: "6px", maxHeight: "300px", overflowY: "auto", fontFamily: "monospace", fontSize: "0.78rem", lineHeight: 1.6 }}>
          {data.alerts_tail.length === 0 ? (
            <div style={{ opacity: 0.6 }}>(无告警记录)</div>
          ) : (
            data.alerts_tail.slice(-20).map((line, i) => (
              <div key={i} style={{ whiteSpace: "pre-wrap", color: line.includes("[CRIT]") ? "#ff8a8a" : "#fbbf24" }}>
                {line}
              </div>
            ))
          )}
          <div style={{ marginTop: "0.5rem", fontSize: "0.72rem", opacity: 0.5 }}>
            服务器:tail -f /var/log/ssp-watchdog-alerts.log 看实时
          </div>
        </div>
      )}
    </div>
  );
}
