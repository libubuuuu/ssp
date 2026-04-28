"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useState, useEffect } from "react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface SnapshotMeta {
  filename: string;
  level: "WARN" | "CRIT";
  size_bytes: number;
  mtime: number;
}

export default function AdminDiagnosePage() {
  const { lang } = useLang();
  const router = useRouter();
  const isEn = lang === "en";
  const [list, setList] = useState<SnapshotMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [openContent, setOpenContent] = useState<string>("");
  const [openBusy, setOpenBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const r = await fetch(`${API_BASE}/api/admin/diagnose-history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 403) {
        alert(isEn ? "Admin only" : "仅管理员可访问");
        router.push("/dashboard");
        return;
      }
      const data = await r.json();
      setList(data.snapshots ?? []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, [isEn, router]);

  const openSnapshot = async (fn: string) => {
    setOpenFile(fn);
    setOpenBusy(true);
    setOpenContent("");
    try {
      const token = localStorage.getItem("token") ?? "";
      const r = await fetch(`${API_BASE}/api/admin/diagnose-snapshot/${fn}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await r.json();
      const text = JSON.stringify(data.data ?? data.raw ?? data, null, 2);
      setOpenContent(text);
    } catch (e) {
      setOpenContent(`(读取失败:${e instanceof Error ? e.message : "?"})`);
    } finally { setOpenBusy(false); }
  };

  const copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(openContent);
      alert(isEn ? "✅ Copied" : "✅ 已复制到剪贴板,粘贴给 Claude 即可精准定位");
    } catch {
      alert(isEn ? "Copy failed,please select manually" : "复制失败,请手动选中复制");
    }
  };

  useEffect(() => { load(); }, [load]);

  const fmtTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleString(isEn ? "en-US" : "zh-CN");
  };
  const fmtSize = (b: number) => {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(2)} MB`;
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f3ed", padding: "2rem" }}>
      <div style={{ maxWidth: 1300, margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ fontSize: "1.8rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>
              {isEn ? "Diagnose History" : "诊断历史"}
            </h1>
            <p style={{ color: "#888", fontSize: "0.85rem", marginTop: "0.4rem" }}>
              {isEn ? "Auto-frozen snapshots when watchdog detects WARN/CRIT — pick any, click Copy, paste to Claude" : "watchdog 每 5 分钟巡检,告警时自动冻结快照 — 任选一份点复制,粘贴给 Claude 精准修复"}
            </p>
          </div>
          <button onClick={load} style={{ background: "none", border: "1px solid #ddd", padding: "0.5rem 1rem", borderRadius: "8px", cursor: "pointer", fontSize: "0.85rem" }}>
            ⟳ {isEn ? "Refresh" : "刷新"}
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: openFile ? "320px 1fr" : "1fr", gap: "1rem" }}>
          {/* 左侧:快照列表 */}
          <div style={{ background: "#fff", borderRadius: "10px", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,0.05)", maxHeight: "75vh", overflowY: "auto" }}>
            {loading ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>{isEn ? "Loading..." : "加载中..."}</div>
            ) : list.length === 0 ? (
              <div style={{ padding: "3rem", textAlign: "center", color: "#0a7", fontSize: "0.9rem" }}>
                {isEn ? "✓ No snapshots yet — system has been healthy" : "✓ 暂无快照 — 生产从未告警"}
              </div>
            ) : (
              list.map(s => (
                <div
                  key={s.filename}
                  onClick={() => openSnapshot(s.filename)}
                  style={{
                    padding: "0.75rem 1rem",
                    borderBottom: "1px solid #f0f0f0",
                    cursor: "pointer",
                    background: openFile === s.filename ? "#fffbe8" : "transparent",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.85rem" }}>
                    <span style={{
                      display: "inline-block", padding: "0.1rem 0.4rem", borderRadius: "4px",
                      background: s.level === "CRIT" ? "#c33" : "#c80",
                      color: "#fff", fontSize: "0.7rem", fontWeight: 600,
                    }}>{s.level}</span>
                    <span style={{ color: "#0d0d0d" }}>{fmtTime(s.mtime)}</span>
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "#888", marginTop: "0.25rem", fontFamily: "monospace" }}>
                    {s.filename} · {fmtSize(s.size_bytes)}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* 右侧:展开内容 */}
          {openFile && (
            <div style={{ background: "#fff", borderRadius: "10px", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,0.05)", display: "flex", flexDirection: "column", maxHeight: "75vh" }}>
              <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid #eee", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontFamily: "monospace", fontSize: "0.82rem", color: "#0d0d0d" }}>{openFile}</span>
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button
                    disabled={openBusy || !openContent}
                    onClick={copyToClipboard}
                    style={{ padding: "0.35rem 0.9rem", border: "1px solid #0d0d0d", background: "#0d0d0d", color: "#fff", borderRadius: "6px", cursor: "pointer", fontSize: "0.78rem", fontWeight: 500 }}
                  >📋 {isEn ? "Copy to clipboard" : "复制全部"}</button>
                  <button
                    onClick={() => { setOpenFile(null); setOpenContent(""); }}
                    style={{ padding: "0.35rem 0.7rem", border: "1px solid #ddd", background: "#fff", borderRadius: "6px", cursor: "pointer", fontSize: "0.78rem" }}
                  >✕</button>
                </div>
              </div>
              <pre style={{
                margin: 0, padding: "1rem", flex: 1, overflow: "auto",
                fontFamily: "monospace", fontSize: "0.72rem", lineHeight: 1.5,
                color: "#0d0d0d", background: "#fafaf7", whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>{openBusy ? (isEn ? "Loading..." : "加载中...") : openContent}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
