"use client";
import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { compressImage } from "@/lib/utils/imageCompress";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const ASPECT_OPTIONS: { key: "9:16" | "16:9" | "1:1"; label: string }[] = [
  { key: "9:16", label: "竖屏 9:16" },
  { key: "16:9", label: "横屏 16:9" },
  { key: "1:1", label: "方屏 1:1" },
];

type HistoryItem = {
  url: string;
  prompt: string;
  ar: string;
  ts: number;
};

const MAX_REFS = 8;

export default function StoryboardPage() {
  const [refUrls, setRefUrls] = useState<string[]>([]);
  const [refPreviews, setRefPreviews] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [aspectRatio, setAspectRatio] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resultUrl, setResultUrl] = useState<string>("");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (refUrls.length >= MAX_REFS) {
      setError(`参考图最多 ${MAX_REFS} 张`);
      return;
    }
    setError("");
    setUploading(true);
    setMsg("正在压缩图片...");
    try {
      const compressed = await compressImage(file);
      setMsg("正在上传...");
      const token = localStorage.getItem("token") ?? "";
      const fd = new FormData();
      fd.append("file", compressed);
      const res = await fetch(`${API_BASE}/api/storyboard/upload/image`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "上传失败");
      setRefUrls((u) => [...u, data.url]);
      setRefPreviews((p) => [...p, URL.createObjectURL(file)]);
      setMsg("");
    } catch (e) {
      setError(errMsg(e));
      setMsg("");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const removeRef = (i: number) => {
    setRefUrls((u) => u.filter((_, idx) => idx !== i));
    setRefPreviews((p) => p.filter((_, idx) => idx !== i));
  };

  const generate = async () => {
    if (refUrls.length === 0) {
      setError("请至少上传 1 张参考图");
      return;
    }
    if (!prompt.trim()) {
      setError("请写提示词");
      return;
    }
    setError("");
    setMsg("");
    setResultUrl("");
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const fd = new FormData();
      refUrls.forEach((u) => fd.append("image_urls", u));
      fd.append("prompt", prompt);
      fd.append("aspect_ratio", aspectRatio);
      const res = await fetch(`${API_BASE}/api/storyboard/generate-frame`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "生成失败");
      if (typeof data.cost === "number" && data.cost > 0) {
        adjustLocalUserCredits(-data.cost);
      }
      setResultUrl(data.image_url);
      setHistory((h) => [
        { url: data.image_url, prompt, ar: aspectRatio, ts: Date.now() },
        ...h.slice(0, 19),
      ]);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const download = async (url: string) => {
    try {
      const r = await fetch(url);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `storyboard_${Date.now()}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      window.open(url, "_blank");
    }
  };

  const useAsRef = async (url: string) => {
    if (refUrls.length >= MAX_REFS) {
      setError(`参考图已满 ${MAX_REFS} 张,先删一张`);
      return;
    }
    setRefUrls((u) => [...u, url]);
    setRefPreviews((p) => [...p, url]);
    setMsg("已把生成图加入参考图列表,可以基于多张图继续改");
    setTimeout(() => setMsg(""), 3000);
  };

  const aspectStyle = (() => {
    if (aspectRatio === "9:16") return { aspectRatio: "9 / 16" };
    if (aspectRatio === "16:9") return { aspectRatio: "16 / 9" };
    return { aspectRatio: "1 / 1" };
  })();

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        background: "#edeae4",
        fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif",
      }}
    >
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>
            分镜图工作台
          </div>
          <h1
            style={{
              fontSize: "1.6rem",
              fontWeight: 400,
              color: "#0d0d0d",
              margin: 0,
              fontFamily: "Georgia,serif",
            }}
          >
            Storyboard <span style={{ fontStyle: "italic" }}>分镜图</span>
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#888", marginTop: "0.4rem" }}>
            上传 1 张参考图 + 写提示词 → GPT-Image 2 出 1 张分镜图(2 积分/张)
          </div>
        </div>

        <div
          style={{
            background: "#fafaf7",
            borderRadius: "24px",
            minHeight: "calc(100vh - 200px)",
            padding: "2rem",
            border: "2px dashed rgba(0,0,0,0.2)",
            display: "flex",
            flexDirection: "column",
            gap: "1.5rem",
          }}
        >
          {/* 当前结果区 */}
          {!resultUrl && !loading && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                minHeight: "400px",
                color: "#bbb",
              }}
            >
              <div style={{ fontSize: "3.5rem", marginBottom: "1rem", color: "#ddd" }}>▦</div>
              <div style={{ fontSize: "0.95rem", color: "#999" }}>右侧上传参考图 + 写提示词</div>
              <div style={{ fontSize: "0.8rem", color: "#bbb", marginTop: "0.5rem" }}>
                生成约 60-130 秒
              </div>
            </div>
          )}

          {loading && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                minHeight: "400px",
              }}
            >
              <div
                style={{
                  width: "44px",
                  height: "44px",
                  border: "3px solid #eee",
                  borderTopColor: "#0d0d0d",
                  borderRadius: "50%",
                  animation: "spin 1s linear infinite",
                }}
              ></div>
              <div style={{ marginTop: "1rem", color: "#666", fontSize: "0.9rem" }}>
                GPT-Image 2 出图中...
              </div>
              <div style={{ marginTop: "0.4rem", color: "#aaa", fontSize: "0.78rem" }}>
                通常 60-130 秒,请勿刷新
              </div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}

          {resultUrl && (
            <div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  marginBottom: "1.25rem",
                }}
              >
                <div
                  style={{
                    ...aspectStyle,
                    maxWidth: aspectRatio === "16:9" ? "720px" : aspectRatio === "9:16" ? "360px" : "500px",
                    width: "100%",
                    background: "#fff",
                    borderRadius: "14px",
                    overflow: "hidden",
                    boxShadow: "0 4px 14px rgba(0,0,0,0.05)",
                    cursor: "pointer",
                  }}
                  onClick={() => download(resultUrl)}
                >
                  <img
                    src={resultUrl}
                    alt="生成结果"
                    style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                  />
                </div>
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "0.75rem",
                  justifyContent: "center",
                  flexWrap: "wrap",
                }}
              >
                <button
                  onClick={() => download(resultUrl)}
                  style={{
                    padding: "0.6rem 1.4rem",
                    background: "#0d0d0d",
                    color: "#fff",
                    border: "none",
                    borderRadius: "999px",
                    cursor: "pointer",
                    fontSize: "0.88rem",
                  }}
                >
                  ⬇ 下载这张
                </button>
                <button
                  onClick={() => useAsRef(resultUrl)}
                  style={{
                    padding: "0.6rem 1.4rem",
                    background: "#fff",
                    color: "#0d0d0d",
                    border: "1px solid #ccc",
                    borderRadius: "999px",
                    cursor: "pointer",
                    fontSize: "0.88rem",
                  }}
                >
                  ↻ 用作新参考图(基于这张接着改)
                </button>
              </div>
            </div>
          )}

          {/* 历史 */}
          {history.length > 0 && (
            <div style={{ marginTop: "1.5rem" }}>
              <div
                style={{
                  fontSize: "0.78rem",
                  color: "#999",
                  marginBottom: "0.6rem",
                  textTransform: "uppercase",
                  letterSpacing: "0.1em",
                }}
              >
                本次会话历史
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))",
                  gap: "0.75rem",
                }}
              >
                {history.map((h, i) => (
                  <div
                    key={i}
                    style={{
                      background: "#fff",
                      borderRadius: "10px",
                      overflow: "hidden",
                      cursor: "pointer",
                      boxShadow: "0 2px 6px rgba(0,0,0,0.04)",
                    }}
                    onClick={() => download(h.url)}
                    title={h.prompt}
                  >
                    <div
                      style={{
                        aspectRatio: h.ar === "9:16" ? "9/16" : h.ar === "16:9" ? "16/9" : "1/1",
                        background: "#f5f5f5",
                      }}
                    >
                      <img
                        src={h.url}
                        alt=""
                        style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      />
                    </div>
                    <div
                      style={{
                        padding: "0.4rem 0.55rem",
                        fontSize: "0.7rem",
                        color: "#666",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {h.prompt}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>

      <aside
        style={{
          width: "340px",
          background: "#fff",
          borderLeft: "1px solid rgba(0,0,0,0.06)",
          padding: "2rem 1.75rem",
          display: "flex",
          flexDirection: "column",
          gap: "1.25rem",
          height: "100vh",
          position: "sticky",
          top: 0,
          overflowY: "auto",
        }}
      >
        <div>
          <div
            style={{
              fontSize: "0.72rem",
              color: "#999",
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              marginBottom: "0.6rem",
            }}
          >
            参考图({refUrls.length}/{MAX_REFS} · 可传产品正/反/模特/背景)
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: "0.5rem",
            }}
          >
            {refPreviews.map((p, i) => (
              <div key={i} style={{ position: "relative", aspectRatio: "1 / 1" }}>
                <img
                  src={p}
                  alt={`参考图 ${i + 1}`}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    borderRadius: "10px",
                    border: "1px solid #eee",
                    display: "block",
                  }}
                />
                <button
                  onClick={() => removeRef(i)}
                  style={{
                    position: "absolute",
                    top: "0.3rem",
                    right: "0.3rem",
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    background: "rgba(0,0,0,0.65)",
                    color: "#fff",
                    border: "none",
                    cursor: "pointer",
                    fontSize: "0.75rem",
                    lineHeight: 1,
                  }}
                >
                  ×
                </button>
                <div
                  style={{
                    position: "absolute",
                    bottom: "0.3rem",
                    left: "0.3rem",
                    background: "rgba(0,0,0,0.6)",
                    color: "#fff",
                    fontSize: "0.65rem",
                    padding: "0.1rem 0.4rem",
                    borderRadius: "999px",
                  }}
                >
                  #{i + 1}
                </div>
              </div>
            ))}
            {refUrls.length < MAX_REFS && (
              <label
                style={{
                  aspectRatio: "1 / 1",
                  border: "2px dashed #ccc",
                  borderRadius: "10px",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: uploading ? "wait" : "pointer",
                  color: "#999",
                  fontSize: "0.7rem",
                  background: "#fafaf7",
                }}
              >
                <input
                  type="file"
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={handleUpload}
                  disabled={uploading}
                />
                <div style={{ fontSize: "1.4rem", color: "#ccc", marginBottom: "0.2rem" }}>+</div>
                {uploading ? "..." : "加图"}
              </label>
            )}
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <div
            style={{
              fontSize: "0.72rem",
              color: "#999",
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              marginBottom: "0.6rem",
            }}
          >
            提示词(必填)
          </div>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例:把模特换成 45 度侧面,微笑看镜头,手里拿着产品自然展示,卧室柔和光线"
            style={{
              width: "100%",
              padding: "0.75rem 0.9rem",
              border: "1px solid #e5e5e5",
              borderRadius: "12px",
              fontSize: "0.88rem",
              minHeight: "150px",
              resize: "vertical",
              fontFamily: "inherit",
              background: "#fff",
              color: "#333",
              flex: 1,
            }}
          />
        </div>

        <div>
          <div
            style={{
              fontSize: "0.72rem",
              color: "#999",
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              marginBottom: "0.6rem",
            }}
          >
            画幅
          </div>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
            {ASPECT_OPTIONS.map((a) => (
              <button
                key={a.key}
                onClick={() => setAspectRatio(a.key)}
                style={{
                  padding: "0.45rem 0.9rem",
                  border: aspectRatio === a.key ? "2px solid #0d0d0d" : "1px solid #e5e5e5",
                  background: aspectRatio === a.key ? "#f9f7f2" : "#fff",
                  borderRadius: "999px",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                  color: "#333",
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>

        {msg && (
          <div
            style={{
              color: "#0a0",
              background: "#eaf7ea",
              padding: "0.7rem",
              borderRadius: "10px",
              fontSize: "0.8rem",
            }}
          >
            {msg}
          </div>
        )}
        {error && (
          <div
            style={{
              color: "#c00",
              background: "#ffeaea",
              padding: "0.7rem",
              borderRadius: "10px",
              fontSize: "0.8rem",
            }}
          >
            {error}
          </div>
        )}

        <button
          onClick={generate}
          disabled={loading || refUrls.length === 0 || !prompt.trim()}
          style={{
            padding: "0.9rem",
            background:
              refUrls.length === 0 || !prompt.trim() || loading ? "#999" : "#0d0d0d",
            color: "#fff",
            border: "none",
            borderRadius: "12px",
            cursor:
              loading || refUrls.length === 0 || !prompt.trim() ? "not-allowed" : "pointer",
            fontSize: "0.95rem",
            fontWeight: 500,
          }}
        >
          {loading ? "生成中..." : `生成分镜图(${refUrls.length} 张参考 · 2 积分)`}
        </button>
      </aside>
    </div>
  );
}
