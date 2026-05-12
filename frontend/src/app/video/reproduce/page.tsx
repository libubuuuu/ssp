"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

// 整页主区直接铺截图,3 张卡叠透明 clickable 区做路由
const HOTSPOTS = [
  { route: "/video/frame-extract", left: "2.4%", top: "8%", width: "30%", height: "75%" },
  { route: "/video/general",       left: "34.7%", top: "8%", width: "31%", height: "75%" },
  { route: "/video-clone-v2",      left: "67%", top: "8%", width: "29.5%", height: "75%" },
];

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, overflowY: "auto", display: "flex", justifyContent: "center", padding: "1.5rem" }}>
        <div style={{ position: "relative", width: "100%", maxWidth: 1467, aspectRatio: "1467/991" }}>
          <img
            src="/reproduce-preview/page_main.png"
            alt="视频复刻 hub"
            style={{ width: "100%", height: "100%", objectFit: "contain", display: "block", userSelect: "none" }}
            draggable={false}
          />
          {HOTSPOTS.map((h) => (
            <div
              key={h.route}
              onClick={() => router.push(h.route)}
              style={{
                position: "absolute",
                left: h.left,
                top: h.top,
                width: h.width,
                height: h.height,
                cursor: "pointer",
                borderRadius: 16,
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(0,0,0,0.04)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
