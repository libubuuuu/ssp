"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { compressVideo } from "@/lib/utils/videoCompress";

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
  const [phase, setPhase] = useState<"idle" | "compress" | "upload">("idle");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [compressProgress, setCompressProgress] = useState(0);
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

  const createNew = async (originalFile: File) => {
    setError("");
    if (!originalFile.type.startsWith("video/")) {
      setError(t("oral.errVideoOnly"));
      return;
    }
    setUploading(true);
    setUploadProgress(0);
    setCompressProgress(0);
    setUploadSpeed("");

    // 七十七续 P5 续:浏览器侧 MediaRecorder 压缩(降到 1280px / 1.5Mbps)。
    // 60s 1080p 视频 50-100MB → 10-20MB,即便走分片也节省 80% 流量。
    // MediaRecorder 不支持 / 压缩失败 → fallback 走原文件,不阻断上传。
    setPhase("compress");
    let file = originalFile;
    try {
      const result = await compressVideo(originalFile, {
        onProgress: (pct) => setCompressProgress(pct),
      });
      // ratio < 0.9 才用压缩版本(< 10% 收益不值得换 webm,部分老设备解码 webm 慢)
      if (result.compressed && result.ratio < 0.9) {
        file = result.file;
      }
    } catch {
      // 压缩本身已 try/catch fallback,这里仅防御
    }
    setCompressProgress(100);
    setPhase("upload");

    // 七十七续 P5:分片上传(YouTube/OSS 标配)
    // 单片 5MB 远小于 nginx client_max_body_size,带宽再差也只是慢一点不会失败
    const CHUNK_SIZE = 5 * 1024 * 1024;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const uploadId = Array.from(crypto.getRandomValues(new Uint8Array(8)))
      .map(b => b.toString(16).padStart(2, "0")).join("");
    const startTime = Date.now();

    // 八十四续:并发上传(限 3),家用上行 3-5x 提速。
    // 后端在"最后一片"到达时合并,所以 0..N-2 并发,最后一片串行触发合并。
    const CONCURRENCY = 3;
    let sentBytesAtomic = 0;
    const updateProgress = (delta: number) => {
      sentBytesAtomic += delta;
      setUploadProgress((sentBytesAtomic / file.size) * 100);
      const elapsed = (Date.now() - startTime) / 1000;
      if (elapsed > 0.5) {
        const mbps = (sentBytesAtomic / 1024 / 1024) / elapsed;
        const remainSec = (file.size - sentBytesAtomic) / Math.max(1, sentBytesAtomic / elapsed);
        setUploadSpeed(`${mbps.toFixed(1)} MB/s · ${t("oral.eta")} ${Math.max(1, Math.ceil(remainSec))}s`);
      }
    };

    const sendOneChunk = async (i: number): Promise<{ status: string; session_id?: string }> => {
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
      let lastErr = "";
      while (attempt < 3) {
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
            // 4xx 不重试(413/400 重试也没用),5xx / 网络错继续
            if (res.status >= 400 && res.status < 500) throw new Error(lastErr);
            throw new Error(lastErr);
          }
          const data = await res.json();
          updateProgress(end - start);
          return data;
        } catch (e: unknown) {
          attempt++;
          lastErr = e instanceof Error ? e.message : String(e);
          if (attempt >= 3) throw new Error(lastErr);
          await new Promise(r => setTimeout(r, 1000));
        }
      }
      throw new Error(lastErr);
    };

    try {
      // 0..N-2 并发(限 3);N-1 最后一片留到所有其他片完成后单独发(触发后端合并)
      const idxList = Array.from({ length: totalChunks - 1 }, (_, i) => i);
      // 简单 semaphore:每次起 CONCURRENCY 个,等任一完成立即派下一个
      const runConcurrent = async () => {
        let cursor = 0;
        const workers = Array.from({ length: Math.min(CONCURRENCY, idxList.length) }, async () => {
          while (cursor < idxList.length) {
            const my = cursor++;
            await sendOneChunk(idxList[my]);
          }
        });
        await Promise.all(workers);
      };
      await runConcurrent();

      // 最后一片单独发 — 后端在此片合并 + 创建 session
      const finalData = await sendOneChunk(totalChunks - 1);
      if (finalData.status === "completed" && finalData.session_id) {
        setUploadProgress(100);
        setUploadSpeed(t("oral.processing"));
        router.push(`/video/oral-broadcast/${finalData.session_id}`);
        return;
      }
      throw new Error("最后一片返回异常,请重试");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errUploadFail"));
    } finally {
      setUploading(false);
      setPhase("idle");
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
              {uploading
                ? (phase === "compress" ? t("oral.compressing") : t("oral.uploading"))
                : `+ ${t("oral.newSession")}`}
              <input type="file" accept="video/*" disabled={uploading}
                onChange={e => { const f = e.target.files?.[0]; if (f) createNew(f); }}
                style={{ display: "none" }} />
            </label>
            <span style={{ color: "#999", fontSize: "0.85rem" }}>{t("oral.maxDuration")}</span>
          </div>

          {!uploading && (
            <div style={{ color: "#999", fontSize: "0.8rem", marginTop: "0.5rem" }}>
              💡 {t("oral.uploadHint")}
            </div>
          )}

          {uploading && (
            <div>
              <div style={{ height: 8, background: "#eee", borderRadius: 4, overflow: "hidden" }}>
                <div style={{
                  width: `${phase === "compress" ? compressProgress : uploadProgress}%`,
                  height: "100%",
                  background: "#0d0d0d", transition: "width 0.2s",
                }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", color: "#666", marginTop: 4 }}>
                <span>
                  {phase === "compress"
                    ? `${t("oral.compressing")} ${compressProgress.toFixed(0)}%`
                    : `${uploadProgress.toFixed(1)}%`}
                </span>
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
