"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const POS_KEY = "job_panel_btn_pos";
const EDGE_GAP = 16;
const DRAG_THRESHOLD = 5; // px，超过才算拖拽，不触发点击

interface Job {
  id: string;
  type: string;
  title: string;
  status: string;
  created_at: number;
  result?: { image_url?: string; video_url?: string; type?: string };
  error?: string;
  _long_video?: boolean;
  _session_id?: string;
  _route?: string;
}

interface BtnPos { x: number; y: number; side: "left" | "right"; }

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }

function snapToSide(x: number, y: number, w: number, h: number): BtnPos {
  const iw = window.innerWidth;
  const ih = window.innerHeight;
  const isLeft = x + w / 2 < iw / 2;
  return {
    x: isLeft ? EDGE_GAP : iw - w - EDGE_GAP,
    y: clamp(y, 0, ih - h),
    side: isLeft ? "left" : "right",
  };
}

function defaultPos(w: number, h: number): BtnPos {
  // 默认放在右侧，WeChat 按钮（bottom:28, 高约52px）上方 12px 处，避免重叠
  const ih = window.innerHeight;
  const iw = window.innerWidth;
  return {
    x: iw - w - EDGE_GAP,
    y: clamp(ih - (28 + 52 + 12 + h), 0, ih - h),
    side: "right",
  };
}

function loadSavedPos(): BtnPos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as BtnPos;
    if (typeof p.x !== "number" || typeof p.y !== "number" || !p.side) return null;
    return p;
  } catch { return null; }
}

function savePos(p: BtnPos) {
  try { localStorage.setItem(POS_KEY, JSON.stringify(p)); } catch {}
}

export default function JobPanel() {
  const { t } = useLang();
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = localStorage.getItem("jobs_dismissed_sessions");
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr : []);
    } catch { return new Set(); }
  });
  const token = useLocalStorageItem("token");
  const loggedIn = !!token;

  // ── 拖拽状态 ────────────────────────────────────────────────────────────
  const wrapRef = useRef<HTMLDivElement>(null);
  // drag.current 用 ref 避免拖拽中大量 re-render
  const drag = useRef({ active: false, startMouse: { x: 0, y: 0 }, startPos: { x: 0, y: 0 }, moved: 0 });
  const [pos, setPos] = useState<BtnPos>({ x: 0, y: 0, side: "right" });
  const [posReady, setPosReady] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // 初始化位置（读 localStorage 或生成默认值）
  useEffect(() => {
    const el = wrapRef.current;
    const w = el?.offsetWidth || 130;
    const h = el?.offsetHeight || 42;
    const saved = loadSavedPos();
    if (saved) {
      setPos({
        ...saved,
        x: clamp(saved.x, 0, window.innerWidth - w),
        y: clamp(saved.y, 0, window.innerHeight - h),
      });
    } else {
      setPos(defaultPos(w, h));
    }
    setPosReady(true);
  }, []);

  // 窗口缩放时把按钮夹回可视区
  useEffect(() => {
    const onResize = () => {
      setPos(prev => {
        const el = wrapRef.current;
        const w = el?.offsetWidth || 130;
        const h = el?.offsetHeight || 42;
        return { ...prev, x: clamp(prev.x, 0, window.innerWidth - w), y: clamp(prev.y, 0, window.innerHeight - h) };
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── 任务轮询（原有逻辑不变）────────────────────────────────────────────
  useEffect(() => {
    if (!loggedIn) return;
    if (typeof document === "undefined") return;
    const poll = async () => {
      if (document.hidden) return;
      const tk = localStorage.getItem("token") ?? "";
      if (!tk) return;
      try {
        const res = await fetch(`${API_BASE}/api/jobs/list`, { headers: { Authorization: `Bearer ${tk}` } });
        if (!res.ok) return;
        const data = await res.json();
        setJobs(data.jobs ?? []);
      } catch {}
    };
    poll();
    const timer = setInterval(poll, 5000);
    const onVisibility = () => { if (!document.hidden) poll(); };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", onVisibility); };
  }, [loggedIn]);

  // ── 拖拽核心逻辑 ────────────────────────────────────────────────────────
  const moveDrag = useCallback((clientX: number, clientY: number) => {
    if (!drag.current.active) return;
    const dx = clientX - drag.current.startMouse.x;
    const dy = clientY - drag.current.startMouse.y;
    drag.current.moved = Math.max(drag.current.moved, Math.hypot(dx, dy));
    const el = wrapRef.current;
    const w = el?.offsetWidth || 130;
    const h = el?.offsetHeight || 42;
    setPos(prev => ({
      ...prev,
      x: clamp(drag.current.startPos.x + dx, 0, window.innerWidth - w),
      y: clamp(drag.current.startPos.y + dy, 0, window.innerHeight - h),
    }));
  }, []);

  const endDrag = useCallback((clientX: number, clientY: number) => {
    if (!drag.current.active) return;
    drag.current.active = false;
    setIsDragging(false);
    if (drag.current.moved > DRAG_THRESHOLD) {
      const dx = clientX - drag.current.startMouse.x;
      const dy = clientY - drag.current.startMouse.y;
      const el = wrapRef.current;
      const w = el?.offsetWidth || 130;
      const h = el?.offsetHeight || 42;
      const snapped = snapToSide(drag.current.startPos.x + dx, drag.current.startPos.y + dy, w, h);
      setPos(snapped);
      savePos(snapped);
    }
  }, []);

  // mouse 全局监听
  useEffect(() => {
    const onMove = (e: MouseEvent) => moveDrag(e.clientX, e.clientY);
    const onUp   = (e: MouseEvent) => endDrag(e.clientX, e.clientY);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup",   onUp);
    return () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
  }, [moveDrag, endDrag]);

  // touch 全局监听（passive:false 允许 preventDefault 阻止页面滚动）
  useEffect(() => {
    const onMove = (e: TouchEvent) => {
      const tc = e.touches[0];
      moveDrag(tc.clientX, tc.clientY);
      if (drag.current.moved > DRAG_THRESHOLD) e.preventDefault();
    };
    const onEnd = (e: TouchEvent) => {
      const tc = e.changedTouches[0];
      endDrag(tc.clientX, tc.clientY);
    };
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend",  onEnd);
    return () => { document.removeEventListener("touchmove", onMove); document.removeEventListener("touchend", onEnd); };
  }, [moveDrag, endDrag]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (open) return;
    e.preventDefault();
    drag.current = { active: true, startMouse: { x: e.clientX, y: e.clientY }, startPos: { x: pos.x, y: pos.y }, moved: 0 };
    setIsDragging(true);
  }, [open, pos.x, pos.y]);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (open) return;
    const tc = e.touches[0];
    drag.current = { active: true, startMouse: { x: tc.clientX, y: tc.clientY }, startPos: { x: pos.x, y: pos.y }, moved: 0 };
    setIsDragging(true);
  }, [open, pos.x, pos.y]);

  // 短点击（无拖拽）→ 打开面板
  const handleClick = useCallback(() => {
    if (drag.current.moved > DRAG_THRESHOLD) return;
    setOpen(true);
  }, []);

  if (!loggedIn) return null;

  const visibleJobs = jobs.filter(j => !(j._session_id && dismissed.has(j._session_id)));
  const running   = visibleJobs.filter(j => j.status === "running" || j.status === "pending").length;
  const completed = visibleJobs.filter(j => j.status === "completed").length;

  const statusLabel = (s: string) => ({
    pending: t("jobs.statusPending"), running: t("jobs.statusRunning"),
    completed: t("jobs.statusCompleted"), failed: t("jobs.statusFailed"),
  } as Record<string, string>)[s] || s;

  const statusColor = (s: string) => ({
    pending: "#f80", running: "#0080ff", completed: "#0a0", failed: "#c00",
  } as Record<string, string>)[s] || "#888";

  const typeLabel = (typ: string) => ({
    image: t("jobs.typeImage"), video_i2v: t("jobs.typeI2V"),
    video_edit: t("jobs.typeEdit"), video_clone: t("jobs.typeClone"),
    video_clone_v2: "视频复刻", video_general: "图片复刻",
  } as Record<string, string>)[typ] || typ;

  const deleteJob = async (id: string) => {
    const tk = localStorage.getItem("token") || "";
    await fetch(`${API_BASE}/api/jobs/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${tk}` } });
    setJobs(jobs.filter(j => j.id !== id));
  };

  return (
    <>
      {/* ── 可拖拽悬浮按钮 ── */}
      <div
        ref={wrapRef}
        style={{
          position: "fixed",
          left: pos.x,
          top: pos.y,
          zIndex: 9999,
          display: open ? "none" : "block",
          opacity: !posReady ? 0 : isDragging ? 0.7 : 1,
          transition: isDragging
            ? "opacity 0.1s"
            : "left 0.22s cubic-bezier(.25,.46,.45,.94), top 0.22s cubic-bezier(.25,.46,.45,.94), opacity 0.15s",
          userSelect: "none",
          touchAction: "none",
          fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif",
          cursor: isDragging ? "grabbing" : "grab",
        }}
        onMouseDown={handleMouseDown}
        onTouchStart={handleTouchStart}
        onClick={handleClick}
      >
        <button style={{
          background: "#0d0d0d", color: "#fff", border: "none",
          borderRadius: 999, padding: "0.8rem 1.2rem",
          cursor: "inherit",
          boxShadow: "0 4px 16px rgba(0,0,0,0.2)", fontSize: "0.9rem",
          display: "flex", alignItems: "center", gap: "0.5rem",
          pointerEvents: "none", // 事件交给父 div 处理
        }}>
          <span style={{ fontSize: "1.1rem" }}>⚡</span>
          <span>{t("jobs.myTasks")}</span>
          {running > 0 && (
            <span style={{ background: "#ff5252", color: "#fff", borderRadius: 999, padding: "0.1rem 0.5rem", fontSize: "0.75rem", fontWeight: 600 }}>
              {running}
            </span>
          )}
        </button>
      </div>

      {/* ── 任务面板（吸附侧展开，不跟随拖拽位置）── */}
      {open && (
        <div style={{
          position: "fixed",
          bottom: 20,
          ...(pos.side === "right" ? { right: EDGE_GAP } : { left: EDGE_GAP }),
          zIndex: 9999,
          fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif",
        }}>
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
                  const tk = localStorage.getItem("token") || "";
                  try { await fetch(`${API_BASE}/api/jobs/clear`, { method: "POST", headers: { Authorization: `Bearer ${tk}` } }); } catch {}
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
                  {j._long_video ? (
                    <span style={{ position: "absolute", top: 6, right: 6, fontSize: "0.7rem", color: "#0080ff" }}>↗ 打开</span>
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
        </div>
      )}
    </>
  );
}
