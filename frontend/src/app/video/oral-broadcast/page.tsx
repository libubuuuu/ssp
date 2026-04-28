"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface OralSession {
  session_id: string;
  tier: string;
  status: string;
  duration_seconds: number;
  final_video_url?: string | null;
  title: string;
  created_at: string;
}

export default function OralBroadcastListPage() {
  const { t } = useLang();
  const router = useRouter();
  const [sessions, setSessions] = useState<OralSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadSpeed, setUploadSpeed] = useState("");
  const [error, setError] = useState("");

  const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/oral/list`, {
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      if (!res.ok) return;
      const data = await res.json();
      setSessions(data.sessions || []);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => {
    loadSessions();
    const i = setInterval(loadSessions, 5000);
    return () => clearInterval(i);
  }, [loadSessions]);

  const createNew = async (file: File) => {
    setError("");
    if (!file.type.startsWith("video/")) {
      setError(t("oral.errVideoOnly"));
      return;
    }
    setUploading(true);
    setUploadProgress(0);
    setUploadSpeed("");

    // 七十七续 P5:分片上传(YouTube/OSS 标配)
    // 单片 5MB 远小于 nginx client_max_body_size,带宽再差也只是慢一点不会失败
    const CHUNK_SIZE = 5 * 1024 * 1024;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const uploadId = Array.from(crypto.getRandomValues(new Uint8Array(8)))
      .map(b => b.toString(16).padStart(2, "0")).join("");
    const startTime = Date.now();

    try {
      for (let i = 0; i < totalChunks; i++) {
        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, file.size);
        const chunkBlob = file.slice(start, end);

        const fd = new FormData();
        fd.append("chunk", chunkBlob, "chunk");
        fd.append("upload_id", uploadId);
        fd.append("chunk_idx", String(i));
        fd.append("total_chunks", String(totalChunks));
        fd.append("filename", file.name);

        let attempt = 0;
        let succeeded = false;
        let lastErr = "";
        while (attempt < 3 && !succeeded) {
          try {
            const res = await fetch(`${API_BASE}/api/oral/upload-chunk`, {
              method: "POST",
              headers: { Authorization: `Bearer ${token()}` },
              credentials: "include",
              body: fd,
            });
            if (!res.ok) {
              const body = await res.json().catch(() => ({}));
              lastErr = body.detail ?? `分片 ${i + 1}/${totalChunks} HTTP ${res.status}`;
              throw new Error(lastErr);
            }
            const data = await res.json();
            succeeded = true;

            // 进度按已发送字节
            const sentBytes = end;
            setUploadProgress((sentBytes / file.size) * 100);
            const elapsed = (Date.now() - startTime) / 1000;
            if (elapsed > 0.5) {
              const mbps = (sentBytes / 1024 / 1024) / elapsed;
              const remainSec = (file.size - sentBytes) / Math.max(1, sentBytes / elapsed);
              setUploadSpeed(`${mbps.toFixed(1)} MB/s · ${t("oral.eta")} ${Math.max(1, Math.ceil(remainSec))}s`);
            }

            // 最后一片 → 后端合并 + 创建 session
            if (data.status === "completed") {
              setUploadProgress(100);
              setUploadSpeed(t("oral.processing"));
              router.push(`/video/oral-broadcast/${data.session_id}`);
              return;
            }
          } catch (e: unknown) {
            attempt++;
            lastErr = e instanceof Error ? e.message : String(e);
            if (attempt >= 3) throw new Error(lastErr);
            // 重试前等 1s
            await new Promise(r => setTimeout(r, 1000));
          }
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errUploadFail"));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#fbfaf6" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 3rem", maxWidth: 1100 }}>
        <div style={{ marginBottom: "2rem" }}>
          <h1 style={{ fontSize: "2rem", fontWeight: 600, margin: 0 }}>
            🎤 {t("oral.title")}
          </h1>
          <p style={{ color: "#888", fontSize: "0.9rem", marginTop: "0.5rem" }}>
            {t("oral.subtitle")}
          </p>
        </div>

        {error && (
          <div style={{ padding: "0.8rem 1rem", background: "#fee", color: "#c33", borderRadius: 8, marginBottom: "1rem" }}>
            {error}
          </div>
        )}

        <div style={{ marginBottom: "2rem" }}>
          <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: uploading ? "1rem" : 0 }}>
            <label style={{
              display: "inline-block", padding: "0.8rem 1.5rem",
              background: uploading ? "#ddd" : "#0d0d0d", color: "#fff",
              borderRadius: 10, cursor: uploading ? "not-allowed" : "pointer", fontWeight: 500,
            }}>
              {uploading ? t("oral.uploading") : `+ ${t("oral.newSession")}`}
              <input type="file" accept="video/*" disabled={uploading}
                onChange={e => { const f = e.target.files?.[0]; if (f) createNew(f); }}
                style={{ display: "none" }} />
            </label>
            <span style={{ color: "#999", fontSize: "0.85rem" }}>{t("oral.maxDuration")}</span>
          </div>

          {uploading && (
            <div>
              <div style={{ height: 8, background: "#eee", borderRadius: 4, overflow: "hidden" }}>
                <div style={{
                  width: `${uploadProgress}%`, height: "100%",
                  background: "#0d0d0d", transition: "width 0.2s",
                }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "#666", marginTop: 4 }}>
                <span>{uploadProgress.toFixed(1)}%</span>
                <span>{uploadSpeed}</span>
              </div>
            </div>
          )}
        </div>

        {loading ? (
          <div style={{ color: "#888" }}>{t("oral.loading")}</div>
        ) : sessions.length === 0 ? (
          <div style={{ padding: "3rem", background: "#fff", borderRadius: 12, textAlign: "center", color: "#999" }}>
            {t("oral.empty")}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "1rem" }}>
            {sessions.map(s => (
              <div key={s.session_id}
                onClick={() => router.push(`/video/oral-broadcast/${s.session_id}`)}
                style={{ padding: "1rem", background: "#fff", borderRadius: 12, cursor: "pointer", border: "1px solid #eee" }}>
                <div style={{ fontWeight: 500, marginBottom: "0.4rem" }}>{s.title}</div>
                <div style={{ fontSize: "0.8rem", color: "#888" }}>
                  {t(`oral.status.${s.status}`) || s.status}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#aaa", marginTop: "0.4rem" }}>
                  {new Date(s.created_at).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
