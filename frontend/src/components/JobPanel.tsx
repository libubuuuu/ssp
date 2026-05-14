"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Job {
  id: string;
  type: string;
  title: string;
  status: string;
  created_at: number;
  result?: { image_url?: string; video_url?: string; type?: string };
  error?: string;
  // 七十五续:long-video 虚拟 job(后端从 STUDIO_TASKS 合并)
  _long_video?: boolean;
  _session_id?: string;
  _route?: string;
}

export default function JobPanel() {
  const { t } = useLang();
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  // 一键清空:常规 jobs 后端真删,虚拟会话(口播/长视频)往 localStorage 加 dismiss 列表本地隐藏
  const [dismissed, setDismissed] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = localStorage.getItem("jobs_dismissed_sessions");
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr : []);
    } catch { return new Set(); }
  });
  // 通过 useSyncExternalStore 订阅 localStorage["token"];
  // 登录页的 setAuthToken / 登出的 clearAuthSession 都 dispatch user-updated → 自动刷新
  const token = useLocalStorageItem("token");
  const loggedIn = !!token;

  useEffect(() => {
    if (!loggedIn) return;
    if (typeof document === "undefined") return;

    const poll = async () => {
      // tab 隐藏时不 poll(避免多 tab 累积请求触发 nginx 限流)
      if (document.hidden) return;
      const t = localStorage.getItem("token") ?? "";
      if (!t) return;
      try {
        const res = await fetch(`${API_BASE}/api/jobs/list`, {
          headers: { Authorization: `Bearer ${t}` },
        });
        // 401 → AuthFetchInterceptor 会处理 refresh 或清 session,这里跳过本轮即可
        if (!res.ok) return;
        const data = await res.json();
        setJobs(data.jobs ?? []);
      } catch {}
    };

    poll();
    // 5 秒一次(从 3 秒放宽,降低多 tab 时的请求密度)
    const timer = setInterval(poll, 5000);

    // tab 切回前台立刻 poll 一次,UX 跟"实时"等价
    const onVisibility = () => {
      if (!document.hidden) poll();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [loggedIn]);

  if (!loggedIn) return null;

  const visibleJobs = jobs.filter(j => !(j._session_id && dismissed.has(j._session_id)));
  const running = visibleJobs.filter(j => j.status === "running" || j.status === "pending").length;
  const completed = visibleJobs.filter(j => j.status === "completed").length;

  const statusLabel = (s: string) => ({
    pending: t("jobs.statusPending"),
    running: t("jobs.statusRunning"),
    completed: t("jobs.statusCompleted"),
    failed: t("jobs.statusFailed"),
  } as Record<string, string>)[s] || s;

  const statusColor = (s: string) => ({
    pending: "#f80",
    running: "#0080ff",
    completed: "#0a0",
    failed: "#c00",
  } as Record<string, string>)[s] || "#888";

  const typeLabel = (typ: string) => ({
    image: t("jobs.typeImage"),
    video_i2v: t("jobs.typeI2V"),
    video_edit: t("jobs.typeEdit"),
    video_clone: t("jobs.typeClone"),
    video_clone_v2: "视频复刻",  // 2026-05-13:V2 虚拟 job
    video_general: "图片复刻",
  } as Record<string, string>)[typ] || typ;

  const deleteJob = async (id: string) => {
    const token = localStorage.getItem("token") || "";
    await fetch(`${API_BASE}/api/jobs/${id}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    setJobs(jobs.filter(j => j.id !== id));
  };

  return (
    <div style={{ position: "fixed", bottom: 20, right: 20, zIndex: 9999, fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      {!open && (
        <button onClick={() => setOpen(true)} style={{
          background: "#0d0d0d", color: "#fff", border: "none",
          borderRadius: 999, padding: "0.8rem 1.2rem", cursor: "pointer",
          boxShadow: "0 4px 16px rgba(0,0,0,0.2)", fontSize: "0.9rem",
          display: "flex", alignItems: "center", gap: "0.5rem",
        }}>
          <span style={{ fontSize: "1.1rem" }}>⚡</span>
          <span>{t("jobs.myTasks")}</span>
          {running > 0 && (
            <span style={{ background: "#ff5252", color: "#fff", borderRadius: 999, padding: "0.1rem 0.5rem", fontSize: "0.75rem", fontWeight: 600 }}>
              {running}
            </span>
          )}
        </button>
      )}
      {open && (
        <div style={{
          background: "#fff", borderRadius: 16, boxShadow: "0 8px 32px rgba(0,0,0,0.15)",
          width: 360, maxHeight: 500, display: "flex", flexDirection: "column", overflow: "hidden",
        }}>
          <div style={{ padding: "1rem 1.2rem", borderBottom: "1px solid #eee", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>{t("jobs.myTasks")}</div>
              <div style={{ fontSize: "0.75rem", color: "#888", marginTop: 2 }}>
                {running} · {completed}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button onClick={async () => {
                if (!confirm("清空所有任务记录?")) return;
                const t = localStorage.getItem("token") || "";
                try {
                  await fetch(`${API_BASE}/api/jobs/clear`, { method: "POST", headers: { Authorization: `Bearer ${t}` } });
                } catch {}
                // 当前可见的虚拟会话 sid 全部加入 dismissed,持久化
                const newDismissed = new Set(dismissed);
                jobs.forEach(j => { if (j._session_id) newDismissed.add(j._session_id); });
                setDismissed(newDismissed);
                try { localStorage.setItem("jobs_dismissed_sessions", JSON.stringify(Array.from(newDismissed))); } catch {}
                setJobs([]);
              }} style={{ background: "none", border: "1px solid #eee", borderRadius: 8, padding: "0.2rem 0.6rem", fontSize: "0.75rem", cursor: "pointer", color: "#888" }}>
                清空
              </button>
              <button onClick={() => setOpen(false)} style={{ background: "none", border: "none", fontSize: "1.2rem", cursor: "pointer", color: "#666" }}>×</button>
            </div>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0.5rem" }}>
            {visibleJobs.length === 0 && (
              <div style={{ padding: "2rem", textAlign: "center", color: "#999", fontSize: "0.85rem" }}>
                {t("jobs.noTasks")}
              </div>
            )}
            {visibleJobs.map(j => (
              <div key={j.id} style={{
                padding: "0.7rem 0.8rem", borderRadius: 10, marginBottom: 6,
                background: "#fafaf7", position: "relative",
                cursor: (j._long_video || j._route) ? "pointer" : "default",
              }}
                onClick={j._route ? () => { setOpen(false); router.push(j._route!); } : undefined}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ fontSize: "0.82rem", fontWeight: 500, color: "#333" }}>
                    {typeLabel(j.type)}
                    {j.title && j.title !== j.type && <span style={{ color: "#888", fontWeight: 400, marginLeft: 4 }}>· {j.title.slice(0, 20)}</span>}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: statusColor(j.status), fontWeight: 500 }}>
                    {statusLabel(j.status)}
                  </div>
                </div>
                {j.result?.image_url && (
                  <img src={j.result.image_url} alt={j.title || "任务结果"} style={{ width: "100%", borderRadius: 8, marginTop: 6, maxHeight: 120, objectFit: "cover" }} />
                )}
                {j.result?.video_url && (
                  <video src={j.result.video_url} controls style={{ width: "100%", borderRadius: 8, marginTop: 6, maxHeight: 150 }} />
                )}
                {j.error && (
                  <div style={{ fontSize: "0.72rem", color: "#c00", marginTop: 4 }}>
                    {j.error.slice(0, 60)}
                  </div>
                )}
                {/* 七十五续:long-video 不显删除按钮(防误删长任务,id=studio_xxx 也无法走 /api/jobs/{id} DELETE),
                    替换成 ↗ 打开提示;点整条 card 跳转详情页 */}
                {j._long_video ? (
                  <span style={{
                    position: "absolute", top: 6, right: 6,
                    fontSize: "0.7rem", color: "#0080ff",
                  }}>↗ 打开</span>
                ) : (
                  <button onClick={e => { e.stopPropagation(); deleteJob(j.id); }} style={{
                    position: "absolute", top: 4, right: 4, background: "none", border: "none",
                    fontSize: "0.7rem", color: "#999", cursor: "pointer",
                  }}>×</button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
