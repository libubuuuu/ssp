"use client";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://ailixiao.com";

export default function HistoryPage() {
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);

  useEffect(() => {
    const token = localStorage.getItem("token") || "";
    fetch(`${API_BASE}/api/tasks/history`, {
      headers: { "Authorization": `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => { setHistory(data.history || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const moduleLabel: Record<string, string> = {
    "video/image-to-video": "图生视频",
    "video/replace/element": "元素替换",
    "video/clone": "翻拍复刻",
    "image/style": "风格化图片",
    "image/realistic": "写实图片",
    "image/multi-reference": "多参考图",
    "avatar/generate": "数字人",
    "voice/clone": "声音克隆",
    "voice/tts": "语音合成",
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>生成记录</div>
          <h1 style={{ fontSize: "1.6rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>历史<span style={{ fontStyle: "italic" }}> 记录</span></h1>
        </div>

        {loading && <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>加载中...</div>}
        {!loading && history.length === 0 && (
          <div style={{ color: "#999", textAlign: "center", marginTop: "4rem" }}>暂无生成记录</div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
          {history.map((item) => (
            <div key={item.id} onClick={() => setSelected(item)}
              style={{ background: "#fff", borderRadius: "14px", overflow: "hidden", boxShadow: "0 4px 12px rgba(0,0,0,0.06)", cursor: "pointer", transition: "transform 0.15s", }}
              onMouseEnter={e => (e.currentTarget.style.transform = "translateY(-2px)")}
              onMouseLeave={e => (e.currentTarget.style.transform = "translateY(0)")}>
              {item.videos?.[0] && (
                <video src={item.videos[0]} style={{ width: "100%", display: "block", maxHeight: "180px", objectFit: "cover" }} />
              )}
              {item.images?.[0] && !item.videos?.[0] && (
                <img src={item.images[0]} alt="" style={{ width: "100%", display: "block", maxHeight: "180px", objectFit: "cover" }} />
              )}
              {!item.videos?.[0] && !item.images?.[0] && (
                <div style={{ height: "120px", background: "#f5f5f0", display: "flex", alignItems: "center", justifyContent: "center", color: "#ccc", fontSize: "2rem" }}>
                  {item.module?.includes("video") ? "▶" : "🖼"}
                </div>
              )}
              <div style={{ padding: "0.75rem 1rem" }}>
                <div style={{ fontSize: "0.85rem", fontWeight: 500, color: "#333", marginBottom: "0.3rem" }}>{moduleLabel[item.module] || item.module}</div>
                <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: "0.4rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{item.prompt}</div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: "#999" }}>
                  <span>{item.created_at?.slice(0, 16)}</span>
                  <span>{item.cost} 积分</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </main>

      {/* 详情弹窗 */}
      {selected && (
        <div onClick={() => setSelected(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: "2rem" }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: "#fff", borderRadius: "20px", maxWidth: "700px", width: "100%", maxHeight: "90vh", overflow: "auto", padding: "2rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
              <h2 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 500 }}>{moduleLabel[selected.module] || selected.module}</h2>
              <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", fontSize: "1.5rem", cursor: "pointer", color: "#999" }}>×</button>
            </div>

            {selected.videos?.[0] && (
              <video src={selected.videos[0]} controls style={{ width: "100%", borderRadius: "12px", marginBottom: "1rem" }} />
            )}
            {selected.images?.[0] && !selected.videos?.[0] && (
              <img src={selected.images[0]} alt="" style={{ width: "100%", borderRadius: "12px", marginBottom: "1rem" }} />
            )}

            <div style={{ fontSize: "0.88rem", color: "#555", marginBottom: "1rem", lineHeight: 1.6 }}>
              <strong>提示词：</strong>{selected.prompt}
            </div>
            <div style={{ fontSize: "0.82rem", color: "#999", marginBottom: "1.5rem" }}>
              {selected.created_at?.slice(0, 19)} · 消耗 {selected.cost} 积分
            </div>

            <div style={{ display: "flex", gap: "0.75rem" }}>
              {selected.videos?.[0] && (
                <a href={selected.videos[0]} download target="_blank" rel="noreferrer"
                  style={{ flex: 1, padding: "0.75rem", background: "#0d0d0d", color: "#fff", textAlign: "center", borderRadius: "10px", textDecoration: "none", fontSize: "0.88rem" }}>
                  下载视频
                </a>
              )}
              {selected.images?.[0] && (
                <a href={selected.images[0]} download target="_blank" rel="noreferrer"
                  style={{ flex: 1, padding: "0.75rem", background: "#0d0d0d", color: "#fff", textAlign: "center", borderRadius: "10px", textDecoration: "none", fontSize: "0.88rem" }}>
                  下载图片
                </a>
              )}
              <button onClick={() => setSelected(null)}
                style={{ flex: 1, padding: "0.75rem", background: "#f5f5f0", border: "none", borderRadius: "10px", cursor: "pointer", fontSize: "0.88rem", color: "#333" }}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
