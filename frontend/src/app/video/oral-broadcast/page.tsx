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

    // 八十四续 P5:浏览器直传腾讯云 COS,完全绕过 ailixiao.com / CF。
    // 流程:STS 拿临时凭证 → cos-js-sdk-v5 PUT 到 bucket → 调 /finalize-cos 后端拉文件
    // 兜底:STS 失败(COS 未启用 / 503) → fallback 走老 /upload-chunk 路径。
    setPhase("compress");
    let file = originalFile;
    const lower = (originalFile.name || "").toLowerCase();
    const isMp4Family = /\.(mp4|mov|m4v)$/.test(lower) || /^video\/(mp4|quicktime)/.test(originalFile.type);
    if (!isMp4Family) {
      try {
        const result = await compressVideo(originalFile, {
          onProgress: (pct) => setCompressProgress(pct),
        });
        if (result.compressed && result.ratio < 0.9) {
          file = result.file;
        }
      } catch {
        // 压缩失败走原文件
      }
    }
    setCompressProgress(100);
    setPhase("upload");

    // 八十四续 P5':presigned PUT URL(zero deps,绕开任何 SDK)
    // 后端帮签好 URL,浏览器 fetch PUT 文件 → 完成后 /finalize-cos 通知后端
    try {
      const presignRes = await fetch(`${API_BASE}/api/storage/presigned-put`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        credentials: "include",
        body: JSON.stringify({ filename: file.name }),
      });
      if (presignRes.ok) {
        const ps = await presignRes.json();
        const startTime2 = Date.now();
        // XHR PUT(可拿 progress)直传 COS bucket,完全不经过 ailixiao.com / CF
        await new Promise<void>((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("PUT", ps.upload_url);
          xhr.timeout = 300_000;  // 5min,大文件够
          if (xhr.upload) {
            xhr.upload.onprogress = (ev) => {
              if (!ev.lengthComputable) return;
              setUploadProgress((ev.loaded / ev.total) * 100);
              const elapsed = (Date.now() - startTime2) / 1000;
              if (elapsed > 0.5) {
                const mbps = (ev.loaded / 1024 / 1024) / elapsed;
                const remainSec = (ev.total - ev.loaded) / Math.max(1, ev.loaded / elapsed);
                setUploadSpeed(`${mbps.toFixed(1)} MB/s · ${t("oral.eta")} ${Math.max(1, Math.ceil(remainSec))}s`);
              }
            };
          }
          xhr.onload = () => xhr.status >= 200 && xhr.status < 300
            ? resolve() : reject(new Error(`COS PUT ${xhr.status}: ${xhr.responseText.slice(0, 200)}`));
          xhr.onerror = () => reject(new Error("COS PUT 网络错"));
          xhr.ontimeout = () => reject(new Error("COS PUT 超时(5min)"));
          xhr.send(file);
        });
        // 通知后端 finalize:从 COS 拉文件 + 建 session
        const finRes = await fetch(`${API_BASE}/api/oral/finalize-cos`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
          credentials: "include",
          body: JSON.stringify({
            object_key: ps.object_key,
            filename: file.name,
            file_size: file.size,
          }),
        });
        const finData = await finRes.json();
        if (!finRes.ok) throw new Error(finData.detail || "finalize 失败");
        setUploadProgress(100);
        setUploadSpeed(t("oral.processing"));
        router.push(`/video/oral-broadcast/${finData.session_id}`);
        return;
      }
      console.warn("[oral upload] presigned PUT 未启用,fallback 到分片上传");
    } catch (e) {
      console.warn("[oral upload] COS 直传失败,fallback 到分片上传:", e);
    }

    // 八十四续 P4:XHR 替代 fetch + 串行 1 路 + 重试 5 次。
    // 实测用户 fetch+HTTP/2 上 9 个并发 stream 全被 CF RST(ERR_HTTP2_PROTOCOL_ERROR)。
    // XHR 用更老的 API,在某些网络/CF 路径下比 fetch 更稳;串行 1 路降低 stream 数。
    // 单片 2MB 平衡 RTT 数 与 单片传输时间(2MB 在 1Mbps 上行下 ~16s,稳过 CF stream timeout)。
    const CHUNK_SIZE = 2 * 1024 * 1024;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const uploadId = Array.from(crypto.getRandomValues(new Uint8Array(8)))
      .map(b => b.toString(16).padStart(2, "0")).join("");
    const startTime = Date.now();
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

    const sendOneChunk = (i: number): Promise<{ status: string; session_id?: string }> => {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunkBlob = file.slice(start, end);
      const fd = new FormData();
      fd.append("chunk", chunkBlob, "chunk");
      fd.append("upload_id", uploadId);
      fd.append("chunk_idx", String(i));
      fd.append("total_chunks", String(totalChunks));
      fd.append("filename", file.name);

      const tryOnce = (): Promise<{ status: string; session_id?: string }> =>
        new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("POST", `${API_BASE}/api/oral/upload-chunk`);
          xhr.setRequestHeader("Authorization", `Bearer ${token()}`);
          xhr.timeout = 120000;  // 单片 120s 硬超时(2MB 在极慢网下也够)
          xhr.withCredentials = true;
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              try { resolve(JSON.parse(xhr.responseText)); }
              catch { reject(new Error(`分片 ${i + 1}/${totalChunks} 响应解析失败`)); }
            } else {
              let detail = `HTTP ${xhr.status}`;
              try { detail = JSON.parse(xhr.responseText).detail || detail; } catch { /* ignore */ }
              const err = new Error(`分片 ${i + 1}/${totalChunks} ${detail}`);
              (err as Error & { status?: number }).status = xhr.status;
              reject(err);
            }
          };
          xhr.onerror = () => reject(new Error(`分片 ${i + 1}/${totalChunks} 网络错(可能是 CF/HTTP2 RST)`));
          xhr.ontimeout = () => reject(new Error(`分片 ${i + 1}/${totalChunks} 超时 (120s)`));
          xhr.onabort = () => reject(new Error(`分片 ${i + 1}/${totalChunks} 已取消`));
          xhr.send(fd);
        });

      const runWithRetry = async () => {
        let lastErr: Error | null = null;
        for (let attempt = 0; attempt < 5; attempt++) {
          try {
            const data = await tryOnce();
            updateProgress(end - start);
            return data;
          } catch (e) {
            lastErr = e as Error;
            const status = (e as Error & { status?: number }).status;
            // 4xx 不重试(413/400 是后端拒,重试无用)
            if (status && status >= 400 && status < 500) throw e;
            // 网络错 / 5xx:指数退避 1/2/4/8/16 秒
            await new Promise(r => setTimeout(r, 1000 * (1 << attempt)));
          }
        }
        throw lastErr || new Error(`分片 ${i + 1}/${totalChunks} 5 次重试后失败`);
      };
      return runWithRetry();
    };

    try {
      // 串行单连接,最稳,降低 CF 多 stream RST 概率
      for (let i = 0; i < totalChunks; i++) {
        const data = await sendOneChunk(i);
        if (i === totalChunks - 1) {
          if (data.status === "completed" && data.session_id) {
            setUploadProgress(100);
            setUploadSpeed(t("oral.processing"));
            router.push(`/video/oral-broadcast/${data.session_id}`);
            return;
          }
          throw new Error("最后一片返回异常,请重试");
        }
      }
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
