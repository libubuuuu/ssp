"use client";
/**
 * 快速带货 — 简化版工作流
 *
 * 1. 上传产品图(blob preview)
 * 2. 调 /api/ad-video/quick-prompt → AI 吐一个完整带货 prompt
 * 3. 用户在 textarea 编辑 prompt
 * 4. 调 /api/video/image-to-video → 拿 task_id,跳转 /tasks?id=xxx 查看进度
 *
 * 跟 /ad-video 4 步重流程互补:这里是"产品图 + AI 起草 + 编辑 + 一键出视频"快速通道。
 */
import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function QuickAdPage() {
  const router = useRouter();
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState("");
  const [productImageUrl, setProductImageUrl] = useState(""); // fal storage URL,后端返
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState(5);
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      if (imagePreview) URL.revokeObjectURL(imagePreview);
    };
  }, [imagePreview]);

  const handleFile = (f: File | null) => {
    if (!f) return;
    setImageFile(f);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImagePreview(URL.createObjectURL(f));
    // 换图后清空 prompt(避免拿别的图的 prompt 误生成)
    setPrompt("");
    setProductImageUrl("");
    setError("");
  };

  const generatePrompt = async () => {
    if (!imageFile) {
      setError("请先上传产品图");
      return;
    }
    setError(""); setLoading(true); setStatusMsg("AI 正在分析图片生成提示词...");
    try {
      const fd = new FormData();
      fd.append("file", imageFile);
      const token = localStorage.getItem("token") || "";
      const r = await fetch(`${API_BASE}/api/ad-video/quick-prompt`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await r.json();
      if (!r.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "提示词生成失败";
        throw new Error(detail);
      }
      setPrompt(data.prompt);
      setProductImageUrl(data.product_image_url);
      // 1 积分
      if (data.cost) adjustLocalUserCredits(-data.cost);
      setStatusMsg("✅ 已生成,可以编辑后生成视频");
    } catch (e) {
      setError(errMsg(e));
      setStatusMsg("");
    } finally {
      setLoading(false);
    }
  };

  const generateVideo = async () => {
    if (!prompt.trim()) {
      setError("请先生成或填写提示词");
      return;
    }
    if (!productImageUrl) {
      setError("产品图未上传到 fal,请重新生成提示词");
      return;
    }
    setError(""); setLoading(true); setStatusMsg("正在提交视频生成任务...");
    try {
      const token = localStorage.getItem("token") || "";
      const r = await fetch(`${API_BASE}/api/video/image-to-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          image_url: productImageUrl,
          prompt: prompt.trim(),
          duration_sec: duration,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "提交失败");
      if (typeof data.cost === "number" && data.cost > 0) adjustLocalUserCredits(-data.cost);
      const taskId = data.task_id;
      if (!taskId) throw new Error("未获取到任务 ID");
      // 跳转任务详情(已有 /tasks?id=xxx)
      router.push(`/tasks?id=${encodeURIComponent(taskId)}&endpoint=${encodeURIComponent(data.endpoint_tag || "i2v")}`);
    } catch (e) {
      setError(errMsg(e));
      setStatusMsg("");
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>快速带货</div>
          <h1 style={{ fontSize: "1.6rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            产品图 → AI 提示词 → <span style={{ fontStyle: "italic" }}>一键视频</span>
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            上传产品图,AI 自动起草带货视频提示词,可编辑后一键生成视频
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, 1fr) 2fr", gap: "1.5rem", maxWidth: 1100 }}>
          {/* 左:上传 */}
          <div style={{ background: "#fff", borderRadius: 16, padding: "1.5rem", border: "1px solid #eee" }}>
            <div style={{ fontSize: "0.9rem", fontWeight: 600, marginBottom: "0.8rem", color: "#333" }}>1. 上传产品图</div>
            <label style={{ display: "block", width: "100%", aspectRatio: "1", border: "2px dashed #ccc", borderRadius: 12, cursor: "pointer", overflow: "hidden", background: "#fafaf7", marginBottom: "1rem" }}>
              <input ref={fileRef} type="file" accept="image/*" style={{ display: "none" }}
                onChange={e => handleFile(e.target.files?.[0] || null)} />
              {imagePreview
                ? <img src={imagePreview} alt="产品图预览" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999", fontSize: "0.85rem" }}>点击上传产品图</div>}
            </label>

            <button onClick={generatePrompt} disabled={loading || !imageFile}
              style={{
                width: "100%", padding: "0.8rem 1.2rem",
                background: !imageFile ? "#ddd" : "#0d0d0d",
                color: "#fff", border: "none", borderRadius: 10,
                cursor: !imageFile || loading ? "default" : "pointer",
                fontSize: "0.9rem", fontWeight: 500, opacity: loading ? 0.6 : 1,
              }}>
              {loading ? "处理中..." : "🤖 AI 生成提示词(1 积分)"}
            </button>
          </div>

          {/* 右:prompt 编辑 + 视频 */}
          <div style={{ background: "#fff", borderRadius: 16, padding: "1.5rem", border: "1px solid #eee" }}>
            <div style={{ fontSize: "0.9rem", fontWeight: 600, marginBottom: "0.8rem", color: "#333" }}>2. 编辑提示词,然后生成视频</div>

            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} disabled={!productImageUrl && !prompt}
              placeholder="先上传产品图,点 AI 生成提示词;或自行输入完整提示词"
              rows={6}
              style={{
                width: "100%", padding: "0.8rem 1rem",
                border: "1px solid #ddd", borderRadius: 10,
                fontSize: "0.9rem", lineHeight: 1.6, fontFamily: "inherit",
                resize: "vertical", marginBottom: "1rem", boxSizing: "border-box",
                color: "#222", background: !productImageUrl && !prompt ? "#fafafa" : "#fff",
              }} />

            <div style={{ display: "flex", gap: "0.8rem", alignItems: "center", marginBottom: "1rem" }}>
              <span style={{ fontSize: "0.85rem", color: "#666" }}>视频时长:</span>
              <select value={duration} onChange={e => setDuration(Number(e.target.value))}
                style={{ padding: "0.5rem 0.8rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.9rem" }}>
                <option value={5}>5 秒(10 积分)</option>
                <option value={10}>10 秒(20 积分)</option>
              </select>
            </div>

            {statusMsg && (
              <div style={{
                padding: "0.6rem 0.9rem", marginBottom: "0.8rem",
                background: statusMsg.startsWith("✅") ? "#eaf7ea" : "#fff8ea",
                color: statusMsg.startsWith("✅") ? "#0a7" : "#7a5400",
                borderRadius: 8, fontSize: "0.85rem",
              }}>{statusMsg}</div>
            )}

            {error && (
              <div style={{
                padding: "0.6rem 0.9rem", marginBottom: "0.8rem",
                background: "#fee", color: "#c00", borderRadius: 8, fontSize: "0.85rem",
              }}>{error}</div>
            )}

            <button onClick={generateVideo} disabled={loading || !prompt.trim() || !productImageUrl}
              style={{
                width: "100%", padding: "0.9rem 1.2rem",
                background: !prompt.trim() || !productImageUrl ? "#ddd" : "#f59e0b",
                color: !prompt.trim() || !productImageUrl ? "#666" : "#000",
                border: "none", borderRadius: 10,
                cursor: !prompt.trim() || !productImageUrl || loading ? "default" : "pointer",
                fontSize: "0.95rem", fontWeight: 600, opacity: loading ? 0.6 : 1,
              }}>
              {loading ? "提交中..." : `🎬 生成视频(${duration === 5 ? 10 : 20} 积分)`}
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
