"use client";
import { useEffect, useState } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface PriceInfo {
  duration_sec: number;
  segments: number;
  segment_length_sec: number;
  resolution: string;
  count: number;
  price_usd: number;
  price_rmb: number;
  credits: number;
}

export default function VideoCloneViduPage() {
  const [duration, setDuration] = useState<number>(23);
  const [resolution, setResolution] = useState<string>("720p");
  const [count, setCount] = useState<number>(1);
  const [price, setPrice] = useState<PriceInfo | null>(null);

  // 价格预估调用骨架端点(目前只有 /price 已实现,upload/generate 返 503)
  useEffect(() => {
    fetch(`${API_BASE}/api/video/clone-vidu/price?duration_sec=${duration}&resolution=${resolution}&count=${count}`)
      .then(r => r.json())
      .then(setPrice)
      .catch(() => {});
  }, [duration, resolution, count]);

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto", maxWidth: 1100, width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>AI 创作工具</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: 0, fontFamily: "Georgia,serif" }}>
            视频<span style={{ fontStyle: "italic" }}> 复刻</span> · Vidu Q2 Pro Reference-to-Video
          </h1>
          <div style={{ fontSize: "0.85rem", color: "#999", marginTop: 4 }}>
            任意时长视频 → 后端切 8s 片段 → 每段 Vidu 生成 → ffmpeg 拼接 → 保留原音轨
          </div>
        </div>

        <div style={{ background: "#fff7ed", border: "1px solid #fed7aa", color: "#9a3412", padding: "1rem 1.2rem", borderRadius: 12, marginBottom: "1.5rem" }}>
          <div style={{ fontSize: "0.95rem", fontWeight: 500, marginBottom: 6 }}>🚧 内测中</div>
          <div style={{ fontSize: "0.85rem", lineHeight: 1.6 }}>
            Vidu Q2 Pro 链路正在跑模型效果验证(单段 ¥2.2 测试)。<br />
            测试通过后会上线完整功能(上传 + 切片 + AI prompt + @ 提示 + 任务历史)。<br />
            目前只有「价格预估」可用,生成功能开发中。
          </div>
        </div>

        <div style={{ background: "#fff", borderRadius: 14, padding: "1.4rem 1.6rem", marginBottom: "1.2rem" }}>
          <div style={{ fontSize: "0.9rem", color: "#666", marginBottom: "0.8rem", fontWeight: 500 }}>价格预估器(模型设计验证)</div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>原视频时长(秒)</div>
              <input type="number" min={1} max={600} value={duration} onChange={e => setDuration(parseInt(e.target.value) || 1)}
                style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem", width: 100 }} />
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>分辨率</div>
              <select value={resolution} onChange={e => setResolution(e.target.value)}
                style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value="360p">360p ($0.10/段)</option>
                <option value="540p">540p ($0.20/段)</option>
                <option value="720p">720p ($0.30/段) ⭐ 主推</option>
                <option value="1080p">1080p (按秒,贵)</option>
              </select>
            </div>
            <div>
              <div style={{ fontSize: "0.78rem", color: "#666", marginBottom: 4 }}>生成数量</div>
              <select value={count} onChange={e => setCount(parseInt(e.target.value))}
                style={{ padding: "0.4rem 0.6rem", border: "1px solid #ddd", borderRadius: 6, fontSize: "0.9rem" }}>
                <option value={1}>1 条</option>
                <option value={2}>2 条</option>
                <option value={3}>3 条</option>
                <option value={4}>4 条</option>
              </select>
            </div>
          </div>
          {price && (
            <div style={{ background: "#f0f7fb", padding: "0.8rem 1rem", borderRadius: 8, fontSize: "0.9rem", color: "#456", lineHeight: 1.6 }}>
              该视频将切为 <b>{price.segments}</b> 段(每段 ≤{price.segment_length_sec}s),
              {price.count > 1 && ` 共 ${price.count} 条 / `}
              预计成本 <b>¥{price.price_rmb}</b> · 消耗 <b>{price.credits}</b> 积分
              <div style={{ fontSize: "0.78rem", color: "#789", marginTop: 4 }}>
                (单段 ${price.resolution === "1080p" ? "1.00" : price.resolution === "720p" ? "0.30" : price.resolution === "540p" ? "0.20" : "0.10"} 定额,跑满 8 秒最划算)
              </div>
            </div>
          )}
        </div>

        <div style={{ fontSize: "0.78rem", color: "#999", marginTop: 24 }}>
          想用 Seedance 旧版?后台已默认下线(P219),如需启用 env 设 ENABLE_SEEDANCE_VIDEO_CLONE=true。
        </div>
      </main>
    </div>
  );
}
