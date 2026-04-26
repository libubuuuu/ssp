"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Session {
  session_id: string;
  status: string;
  duration: number;
  total_segments: number;
  completed_segments: number;
  final_url?: string;
  created_at: number;
  remaining_days: number;
}

export default function StudioListPage() {
  const { t } = useLang();
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0); // 0-100
  const [uploadSpeed, setUploadSpeed] = useState("");
  const [error, setError] = useState("");

  const token = () => localStorage.getItem("token") || "";

  const loadSessions = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/studio/list`, {
        headers: { Authorization: `Bearer ${token()}` },
      });
      if (!res.ok) return;
      const data = await res.json();
      setSessions(data.sessions || []);
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => {
    loadSessions();
    const t = setInterval(loadSessions, 5000);
    return () => clearInterval(t);
  }, []);

  const createNew = (file: File) => {
    setError("");
    setCreating(true);
    setUploadProgress(0);
    setUploadSpeed("");

    const fd = new FormData();
    fd.append("file", file);

    // 用 XMLHttpRequest 才能拿到 upload progress(fetch 不支持)
    const xhr = new XMLHttpRequest();
    const startTime = Date.now();

    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = (e.loaded / e.total) * 100;
      setUploadProgress(pct);
      // 实时速度估算
      const elapsed = (Date.now() - startTime) / 1000;
      if (elapsed > 0.5) {
        const mbps = (e.loaded / 1024 / 1024) / elapsed;
        const remainSec = (e.total - e.loaded) / (e.loaded / elapsed);
        setUploadSpeed(`${mbps.toFixed(1)} MB/s · 剩余约 ${Math.max(1, Math.ceil(remainSec))} 秒`);
      }
    };

    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText || "{}");
        if (xhr.status >= 200 && xhr.status < 300) {
          setUploadProgress(100);
          setUploadSpeed("处理中...");
          router.push(`/video/studio/${data.session_id}`);
        } else {
          setError(data.detail || `上传失败 (HTTP ${xhr.status})`);
          setCreating(false);
        }
      } catch {
        setError(`上传失败 (HTTP ${xhr.status})`);
        setCreating(false);
      }
    };
    xhr.onerror = () => {
      setError("网络错误,请检查网络后重试");
      setCreating(false);
    };
    xhr.onabort = () => {
      setError("上传已取消");
      setCreating(false);
    };

    xhr.open("POST", `${API_BASE}/api/studio/upload`);
    xhr.setRequestHeader("Authorization", `Bearer ${token()}`);
    xhr.send(fd);
  };

  const deleteSession = async (sid: string) => {
    if (!confirm("确认删除这个项目？不可恢复")) return;
    await fetch(`${API_BASE}/api/studio/session/${sid}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token()}` },
    });
    loadSessions();
  };

  const statusLabel = (s: string) => ({
    uploaded: t("studio.statusUploaded"), split: t("studio.statusSplit"), generating: t("studio.statusGenerating"),
    done: t("studio.statusDone"), finished: t("studio.statusFinished"),
  } as any)[s] || s;

  const statusColor = (s: string) => ({
    uploaded: "#888", split: "#f80", generating: "#0080ff",
    done: "#0a0", finished: "#0a0",
  } as any)[s] || "#888";

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
          <div>
            <div style={{ fontSize: "0.85rem", color: "#999" }}>{t("studio.studioVideo")}</div>
            <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: "0.3rem 0", fontFamily: "Georgia,serif" }}>{t("studio.studioLongMain")} <span style={{ fontStyle: "italic" }}>{t("studio.studioLongAccent")}</span></h1>
            <div style={{ fontSize: "0.85rem", color: "#999" }}>{t("studio.studioSubtitle")}</div>
          </div>
          <label style={{ padding: "0.8rem 1.5rem", background: "#0d0d0d", color: "#fff", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem", fontWeight: 500 }}>
            <input type="file" accept="video/*" style={{ display: "none" }} onChange={e => {
              const f = e.target.files?.[0]; if (f) createNew(f);
            }} disabled={creating} />
            {creating ? t("studio.uploading") : t("studio.newProject")}
          </label>
        </div>

        {error && <div style={{ padding: "0.7rem 1rem", background: "#ffeaea", color: "#c00", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{error}</div>}

        {creating && (
          <div style={{ padding: "1rem 1.25rem", background: "#fff", border: "1px solid #e5e2dc", borderRadius: 12, marginBottom: "1rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem", fontSize: "0.88rem" }}>
              <span style={{ color: "#0d0d0d", fontWeight: 500 }}>
                {uploadProgress < 100 ? `上传中 ${uploadProgress.toFixed(1)}%` : "服务器处理中..."}
              </span>
              <span style={{ color: "#999", fontSize: "0.78rem" }}>{uploadSpeed}</span>
            </div>
            <div style={{ height: 8, background: "#e5e2dc", borderRadius: 4, overflow: "hidden" }}>
              <div style={{
                width: `${uploadProgress}%`,
                height: "100%",
                background: uploadProgress >= 100 ? "#0a7" : "#0d0d0d",
                transition: "width 0.2s ease-out",
              }} />
            </div>
          </div>
        )}

        {loading && <div style={{ color: "#999", textAlign: "center", padding: "3rem" }}>{t("studio.loading")}</div>}
        {!loading && sessions.length === 0 && (
          <div style={{ color: "#999", textAlign: "center", padding: "3rem", fontSize: "0.9rem" }}>
            {t("studio.emptyProjects")}
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(340px,1fr))", gap: "1.2rem" }}>
          {sessions.map(s => (
            <div key={s.session_id} style={{ background: "#fff", borderRadius: 14, padding: "1.2rem", border: "1px solid #eee", cursor: "pointer", position: "relative" }}
              onClick={() => router.push(`/video/studio/${s.session_id}`)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <div style={{ fontSize: "0.8rem", color: "#888", fontFamily: "monospace" }}>#{s.session_id}</div>
                <span style={{ fontSize: "0.75rem", color: statusColor(s.status), fontWeight: 500 }}>● {statusLabel(s.status)}</span>
              </div>
              {s.final_url ? (
                <video src={s.final_url} style={{ width: "100%", borderRadius: 8, marginBottom: "0.6rem" }} />
              ) : (
                <div style={{ background: "#fafaf7", borderRadius: 8, padding: "1.5rem 0.5rem", textAlign: "center", marginBottom: "0.6rem", color: "#bbb", fontSize: "0.8rem" }}>
                  {s.status === "finished" ? t("studio.finalVideo") : `${s.total_segments || 0} ${t("studio.segsUnit")} · ${s.completed_segments}/${s.total_segments} ${t("studio.segsFinished")}`}
                </div>
              )}
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "#888" }}>
                <span>{s.duration}{t("studio.durationSec")} · {s.total_segments}{t("studio.segsUnit")}</span>
                <span>{t("studio.remainingDaysLabel")} {s.remaining_days}{t("studio.daysUnit")}</span>
              </div>
              <button onClick={(e) => { e.stopPropagation(); deleteSession(s.session_id); }}
                style={{ position: "absolute", top: 8, right: 8, background: "rgba(255,255,255,0.9)", border: "1px solid #eee", borderRadius: 6, padding: "0.2rem 0.4rem", fontSize: "0.7rem", cursor: "pointer", color: "#c00" }}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
