"use client";
import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";
import { compressImage } from "@/lib/utils/imageCompress";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Frame = {
  idx: number;
  title: string;
  purpose: string;
  shot_type: string;
  visual_prompt?: string;
  prompt?: string;
  image_url: string | null;
  error: string | null;
};

type Result = {
  overall_theme: string;
  frames: Frame[];
  success_count: number;
  total_count: number;
  cost?: number;
};

const FRAME_OPTIONS = [3, 5, 6, 8, 10, 12];
const ASPECT_OPTIONS: { key: "9:16" | "16:9" | "1:1"; label: string }[] = [
  { key: "9:16", label: "竖屏 9:16" },
  { key: "16:9", label: "横屏 16:9" },
  { key: "1:1", label: "方屏 1:1" },
];

export default function StoryboardPage() {
  const [refUrl, setRefUrl] = useState<string>("");
  const [refPreview, setRefPreview] = useState<string>("");
  const [description, setDescription] = useState("");
  const [nFrames, setNFrames] = useState<number>(5);
  const [aspectRatio, setAspectRatio] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
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
      setRefUrl(data.url);
      setRefPreview(URL.createObjectURL(file));
      setMsg("");
    } catch (e) {
      setError(errMsg(e));
      setMsg("");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const generate = async () => {
    if (!refUrl) {
      setError("请先上传参考图");
      return;
    }
    setError("");
    setMsg("");
    setResult(null);
    setLoading(true);
    try {
      const token = localStorage.getItem("token") ?? "";
      const fd = new FormData();
      fd.append("image_url", refUrl);
      fd.append("description", description);
      fd.append("n_frames", String(nFrames));
      fd.append("aspect_ratio", aspectRatio);
      const res = await fetch(`${API_BASE}/api/storyboard/generate`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? "生成失败");
      if (typeof data.cost === "number" && data.cost > 0) {
        adjustLocalUserCredits(-data.cost);
      }
      setResult(data as Result);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  const downloadOne = async (url: string, idx: number) => {
    try {
      const r = await fetch(url);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `storyboard_${idx}_${Date.now()}.png`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      window.open(url, "_blank");
    }
  };

  const downloadAll = async () => {
    if (!result) return;
    for (const f of result.frames) {
      if (f.image_url) {
        await downloadOne(f.image_url, f.idx);
        await new Promise((r) => setTimeout(r, 300));
      }
    }
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
            一张参考图 + 文字描述 → AI 自动拆出 N 段不同景别/角度/动作的分镜图(主体保持一致)
          </div>
        </div>

        <div
          style={{
            background: "#fafaf7",
            backgroundImage:
              "linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
            borderRadius: "24px",
            minHeight: "calc(100vh - 200px)",
            padding: "2rem",
            border: "2px dashed rgba(0,0,0,0.2)",
          }}
        >
          {!result && !loading && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                minHeight: "500px",
                color: "#bbb",
              }}
            >
              <div style={{ fontSize: "3.5rem", marginBottom: "1rem", color: "#ddd" }}>▦</div>
              <div style={{ fontSize: "0.95rem", color: "#999" }}>右侧上传参考图开始</div>
              <div style={{ fontSize: "0.8rem", color: "#bbb", marginTop: "0.5rem" }}>
                生成约 30-60 秒
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
                minHeight: "500px",
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
                AI 正在拆分镜并生成 {nFrames} 张分镜图...
              </div>
              <div style={{ marginTop: "0.4rem", color: "#aaa", fontSize: "0.78rem" }}>
                通常 30-60 秒,请勿刷新页面
              </div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}

          {result && (
            <div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "1.25rem",
                  flexWrap: "wrap",
                  gap: "0.75rem",
                }}
              >
                <div>
                  <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.2rem" }}>
                    整体主题
                  </div>
                  <div style={{ fontSize: "1.05rem", color: "#0d0d0d", fontWeight: 500 }}>
                    {result.overall_theme || "(未命名)"}
                  </div>
                  <div style={{ fontSize: "0.78rem", color: "#888", marginTop: "0.25rem" }}>
                    成功 {result.success_count}/{result.total_count} 段 · 比例 {aspectRatio}
                  </div>
                </div>
                <button
                  onClick={downloadAll}
                  style={{
                    padding: "0.6rem 1.1rem",
                    background: "#0d0d0d",
                    color: "#fff",
                    border: "none",
                    borderRadius: "999px",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  ⬇ 全部下载
                </button>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns:
                    aspectRatio === "9:16"
                      ? "repeat(auto-fill,minmax(220px,1fr))"
                      : aspectRatio === "16:9"
                      ? "repeat(auto-fill,minmax(340px,1fr))"
                      : "repeat(auto-fill,minmax(260px,1fr))",
                  gap: "1.25rem",
                }}
              >
                {result.frames.map((f) => (
                  <div
                    key={f.idx}
                    style={{
                      background: "#fff",
                      borderRadius: "16px",
                      overflow: "hidden",
                      boxShadow: "0 4px 14px rgba(0,0,0,0.05)",
                    }}
                  >
                    <div
                      style={{
                        ...aspectStyle,
                        background: "#f5f5f5",
                        position: "relative",
                        cursor: f.image_url ? "pointer" : "default",
                      }}
                      onClick={() => f.image_url && downloadOne(f.image_url, f.idx)}
                    >
                      {f.image_url ? (
                        <>
                          <img
                            src={f.image_url}
                            alt={f.title}
                            style={{
                              width: "100%",
                              height: "100%",
                              objectFit: "cover",
                              display: "block",
                            }}
                          />
                          <div
                            style={{
                              position: "absolute",
                              top: "0.5rem",
                              left: "0.5rem",
                              background: "rgba(0,0,0,0.7)",
                              color: "#fff",
                              padding: "0.2rem 0.55rem",
                              borderRadius: "999px",
                              fontSize: "0.7rem",
                            }}
                          >
                            #{f.idx}
                          </div>
                          <div
                            style={{
                              position: "absolute",
                              top: "0.5rem",
                              right: "0.5rem",
                              background: "rgba(0,0,0,0.6)",
                              color: "#fff",
                              padding: "0.2rem 0.55rem",
                              borderRadius: "999px",
                              fontSize: "0.7rem",
                            }}
                          >
                            ⬇
                          </div>
                        </>
                      ) : (
                        <div
                          style={{
                            position: "absolute",
                            inset: 0,
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "center",
                            justifyContent: "center",
                            color: "#bbb",
                            padding: "1rem",
                            textAlign: "center",
                          }}
                        >
                          <div style={{ fontSize: "1.6rem", marginBottom: "0.5rem" }}>✕</div>
                          <div style={{ fontSize: "0.8rem" }}>段 {f.idx} 生成失败</div>
                          <div
                            style={{
                              fontSize: "0.7rem",
                              marginTop: "0.4rem",
                              color: "#999",
                            }}
                          >
                            {(f.error ?? "").slice(0, 80)}
                          </div>
                        </div>
                      )}
                    </div>
                    <div style={{ padding: "0.85rem 1rem" }}>
                      <div
                        style={{
                          fontSize: "0.92rem",
                          fontWeight: 500,
                          color: "#0d0d0d",
                          marginBottom: "0.25rem",
                        }}
                      >
                        {f.title}
                      </div>
                      <div style={{ fontSize: "0.72rem", color: "#888" }}>
                        {f.purpose} · {f.shot_type}
                      </div>
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
            参考图(必填)
          </div>
          {refPreview ? (
            <div style={{ position: "relative", width: "100%" }}>
              <img
                src={refPreview}
                alt="参考图"
                style={{
                  width: "100%",
                  borderRadius: "12px",
                  border: "1px solid #eee",
                  display: "block",
                }}
              />
              <button
                onClick={() => {
                  setRefUrl("");
                  setRefPreview("");
                }}
                style={{
                  position: "absolute",
                  top: "0.4rem",
                  right: "0.4rem",
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  background: "rgba(0,0,0,0.65)",
                  color: "#fff",
                  border: "none",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                ×
              </button>
            </div>
          ) : (
            <label
              style={{
                width: "100%",
                minHeight: "120px",
                border: "2px dashed #ccc",
                borderRadius: "12px",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                cursor: uploading ? "wait" : "pointer",
                color: "#999",
                fontSize: "0.85rem",
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
              <div style={{ fontSize: "1.6rem", color: "#ccc", marginBottom: "0.4rem" }}>+</div>
              {uploading ? "处理中..." : "点击上传参考图"}
              <div style={{ fontSize: "0.72rem", color: "#bbb", marginTop: "0.3rem" }}>
                支持产品图 / 模特+产品 / 场景图
              </div>
            </label>
          )}
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
            创作描述(可选)
          </div>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="例:女性塑身衣爆款带货短视频,展示 360 度收紧效果 + 卧室场景"
            style={{
              width: "100%",
              padding: "0.75rem 0.9rem",
              border: "1px solid #e5e5e5",
              borderRadius: "12px",
              fontSize: "0.85rem",
              minHeight: "90px",
              resize: "vertical",
              fontFamily: "inherit",
              background: "#fff",
              color: "#333",
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
            分镜数
          </div>
          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
            {FRAME_OPTIONS.map((n) => (
              <button
                key={n}
                onClick={() => setNFrames(n)}
                style={{
                  padding: "0.45rem 0.9rem",
                  border: nFrames === n ? "2px solid #0d0d0d" : "1px solid #e5e5e5",
                  background: nFrames === n ? "#f9f7f2" : "#fff",
                  borderRadius: "999px",
                  cursor: "pointer",
                  fontSize: "0.82rem",
                  color: "#333",
                  minWidth: "44px",
                }}
              >
                {n}
              </button>
            ))}
          </div>
          <div style={{ fontSize: "0.7rem", color: "#888", marginTop: "0.4rem" }}>
            {nFrames > 6 ? "12 积分/单(7-12 段)" : "8 积分/单(2-6 段)"}
          </div>
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
          disabled={loading || !refUrl}
          style={{
            padding: "0.9rem",
            background: !refUrl || loading ? "#999" : "#0d0d0d",
            color: "#fff",
            border: "none",
            borderRadius: "12px",
            cursor: loading || !refUrl ? "not-allowed" : "pointer",
            fontSize: "0.95rem",
            fontWeight: 500,
          }}
        >
          {loading ? "生成中..." : `生成 ${nFrames} 段分镜图`}
        </button>
      </aside>
    </div>
  );
}
