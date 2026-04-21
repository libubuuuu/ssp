"use client";
import { useState, useRef, useEffect } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://ailixiao.com";

interface Card {
  id: string;
  imageFile: File | null;
  imagePreview: string;
  prompt: string;
  duration: number;
  jobId: string;
  status: string; // idle / uploading / pending / running / completed / failed
  progress: string;
  resultUrl: string;
  error: string;
}

export default function VideoPage() {
  const [cards, setCards] = useState<Card[]>([newCard()]);
  const pollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  function newCard(): Card {
    return {
      id: Math.random().toString(36).slice(2, 10),
      imageFile: null, imagePreview: "", prompt: "", duration: 5,
      jobId: "", status: "idle", progress: "", resultUrl: "", error: "",
    };
  }

  useEffect(() => () => {
    Object.values(pollRefs.current).forEach(t => clearInterval(t));
  }, []);

  const updateCard = (id: string, patch: Partial<Card>) => {
    setCards(prev => prev.map(c => c.id === id ? { ...c, ...patch } : c));
  };

  const addCard = () => setCards(prev => [...prev, newCard()]);

  const removeCard = (id: string) => {
    if (pollRefs.current[id]) {
      clearInterval(pollRefs.current[id]);
      delete pollRefs.current[id];
    }
    setCards(prev => prev.filter(c => c.id !== id));
    if (cards.length <= 1) setCards([newCard()]);
  };

  const uploadImage = async (file: File): Promise<string> => {
    const token = localStorage.getItem("token") || "";
    const fd = new FormData();
    fd.append("file", file);
    const r = await fetch(`${API_BASE}/api/video/upload/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "图片上传失败");
    return d.url;
  };

  const startGenerate = async (card: Card) => {
    if (!card.imageFile) {
      updateCard(card.id, { error: "请上传图片" });
      return;
    }
    updateCard(card.id, { error: "", status: "uploading", progress: "上传图片中..." });
    try {
      const imgUrl = await uploadImage(card.imageFile);
      updateCard(card.id, { progress: "提交任务中..." });
      const token = localStorage.getItem("token") || "";
      const res = await fetch(`${API_BASE}/api/jobs/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          type: "video_i2v",
          title: card.prompt.slice(0, 30) || "图生视频",
          params: { image_url: imgUrl, prompt: card.prompt, duration_sec: card.duration },
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "提交失败");
      updateCard(card.id, { jobId: d.job_id, status: "pending", progress: "排队中..." });
      startPolling(card.id, d.job_id);
    } catch (e: any) {
      updateCard(card.id, { status: "failed", error: e.message, progress: "" });
    }
  };

  const startPolling = (cardId: string, jobId: string) => {
    if (pollRefs.current[cardId]) clearInterval(pollRefs.current[cardId]);
    const token = localStorage.getItem("token") || "";
    let sec = 0;
    pollRefs.current[cardId] = setInterval(async () => {
      sec += 5;
      try {
        const r = await fetch(`${API_BASE}/api/jobs/${jobId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const j = await r.json();
        if (j.status === "completed" && j.result?.video_url) {
          updateCard(cardId, { status: "completed", resultUrl: j.result.video_url, progress: "" });
          clearInterval(pollRefs.current[cardId]);
          delete pollRefs.current[cardId];
        } else if (j.status === "failed") {
          updateCard(cardId, { status: "failed", error: j.error || "失败", progress: "" });
          clearInterval(pollRefs.current[cardId]);
          delete pollRefs.current[cardId];
        } else {
          const mins = Math.floor(sec / 60), s = sec % 60;
          updateCard(cardId, { status: j.status === "running" ? "running" : "pending", progress: `生成中 ${mins}分${s}秒...` });
        }
      } catch {}
    }, 5000);
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>视频创作</div>
            <h1 style={{ fontSize: "1.6rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>图生<span style={{ fontStyle: "italic" }}> 视频</span></h1>
            <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>上传首帧图，AI 生成动态视频（支持多窗口并行）</div>
          </div>
          <button onClick={addCard} style={{
            padding: "0.7rem 1.3rem", background: "#0d0d0d", color: "#fff",
            border: "none", borderRadius: 10, cursor: "pointer", fontSize: "0.9rem", fontWeight: 500,
          }}>+ 新建窗口</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(340px,1fr))", gap: "1.2rem" }}>
          {cards.map((c, idx) => (
            <div key={c.id} style={{ background: "#fff", borderRadius: 16, padding: "1.2rem", border: "1px solid #eee", position: "relative" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
                <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#333" }}>窗口 #{idx + 1}</div>
                <button onClick={() => removeCard(c.id)} style={{ background: "none", border: "none", color: "#999", cursor: "pointer", fontSize: "0.85rem" }}>✕ 关闭</button>
              </div>

              {/* 图片 */}
              <div style={{ marginBottom: "0.8rem" }}>
                <label style={{ display: "block", width: "100%", aspectRatio: "1", border: "2px dashed #ccc", borderRadius: 12, cursor: "pointer", overflow: "hidden", background: "#fafaf7" }}>
                  <input type="file" accept="image/*" style={{ display: "none" }}
                    onChange={e => {
                      const f = e.target.files?.[0]; if (!f) return;
                      updateCard(c.id, { imageFile: f, imagePreview: URL.createObjectURL(f) });
                    }} />
                  {c.imagePreview
                    ? <img src={c.imagePreview} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999", fontSize: "0.85rem" }}>点击上传首帧图</div>}
                </label>
              </div>

              {/* 时长 */}
              <div style={{ marginBottom: "0.8rem" }}>
                <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>时长</div>
                <select value={c.duration} onChange={e => updateCard(c.id, { duration: Number(e.target.value) })}
                  style={{ width: "100%", padding: "0.5rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", background: "#fff" }}>
                  <option value={5}>5秒</option>
                  <option value={10}>10秒</option>
                </select>
              </div>

              {/* 台词/动作 */}
              <div style={{ marginBottom: "0.8rem" }}>
                <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>动作 / 台词（写台词可生成人声）</div>
                <textarea value={c.prompt} onChange={e => updateCard(c.id, { prompt: e.target.value })}
                  placeholder="例：模特说「这款内衣很舒适」"
                  style={{ width: "100%", padding: "0.5rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.85rem", minHeight: 60, resize: "vertical", fontFamily: "inherit", boxSizing: "border-box" }} />
              </div>

              {/* 状态/结果 */}
              {c.progress && <div style={{ fontSize: "0.8rem", color: "#f80", marginBottom: "0.5rem" }}>⏳ {c.progress}</div>}
              {c.error && <div style={{ fontSize: "0.8rem", color: "#c00", background: "#ffeaea", padding: "0.5rem", borderRadius: 6, marginBottom: "0.5rem" }}>{c.error}</div>}
              {c.resultUrl && (
                <div style={{ marginBottom: "0.5rem" }}>
                  <div style={{ fontSize: "0.8rem", color: "#0a0", marginBottom: 4 }}>✅ 完成</div>
                  <video src={c.resultUrl} controls style={{ width: "100%", borderRadius: 8 }} />
                  <a href={c.resultUrl} download target="_blank" style={{ display: "inline-block", marginTop: 6, fontSize: "0.75rem", color: "#0d0d0d" }}>⬇ 下载视频</a>
                </div>
              )}

              {/* 按钮 */}
              <button onClick={() => startGenerate(c)}
                disabled={c.status === "uploading" || c.status === "pending" || c.status === "running"}
                style={{
                  width: "100%", padding: "0.7rem", background: (c.status === "uploading" || c.status === "pending" || c.status === "running") ? "#999" : "#0d0d0d",
                  color: "#fff", border: "none", borderRadius: 10, cursor: "pointer", fontSize: "0.88rem", fontWeight: 500,
                }}>
                {c.status === "completed" ? "重新生成" : (c.status === "pending" || c.status === "running" || c.status === "uploading") ? "生成中..." : "开始生成"}
              </button>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
