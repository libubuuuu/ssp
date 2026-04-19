"use client";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

export default function HistoryPage() {
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("token") || "";
    fetch(`${API_BASE}/api/tasks/history`, {
      headers: { "Authorization": `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => { setHistory(data.history || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

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
            <div key={item.id} style={{ background: "#fff", borderRadius: "14px", overflow: "hidden", boxShadow: "0 4px 12px rgba(0,0,0,0.06)" }}>
              {item.videos && item.videos[0] && (
                <video src={item.videos[0]} controls style={{ width: "100%", display: "block" }} />
              )}
              {item.images && item.images[0] && !item.videos?.[0] && (
                <img src={item.images[0]} alt="" style={{ width: "100%", display: "block" }} />
              )}
              <div style={{ padding: "0.75rem 1rem" }}>
                <div style={{ fontSize: "0.82rem", color: "#333", marginBottom: "0.3rem" }}>{item.prompt}</div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem", color: "#999" }}>
                  <span>{item.module}</span>
                  <span>{item.cost} 积分</span>
                </div>
                <div style={{ fontSize: "0.7rem", color: "#bbb", marginTop: "0.25rem" }}>{item.created_at?.slice(0, 19)}</div>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
