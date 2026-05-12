"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

// 整页主区直接铺截图,3 张卡叠 clickable 区做路由 + hover 时"陷进去"触感
// hotspot 收小一圈圈进卡内,hover 用 inset shadow + 微缩模拟向内压
const HOTSPOTS = [
  { route: "/video/frame-extract", left: "3.5%",  top: "10%", width: "28%",   height: "71%" },
  { route: "/video/general",       left: "35.5%", top: "10%", width: "29%",   height: "71%" },
  { route: "/video-clone-v2",      left: "67.8%", top: "10%", width: "28.5%", height: "71%" },
];

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        .pressHotspot {
          transition: box-shadow 0.22s ease, transform 0.22s ease, filter 0.22s ease;
        }
        .pressHotspot:hover {
          box-shadow: inset 0 6px 22px rgba(0,0,0,0.12), inset 0 -2px 6px rgba(0,0,0,0.05);
          transform: scale(0.985);
          filter: brightness(0.985);
        }
        .pressHotspot:active {
          box-shadow: inset 0 8px 28px rgba(0,0,0,0.18), inset 0 -2px 6px rgba(0,0,0,0.06);
          transform: scale(0.975);
        }
      `}</style>
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
              className="pressHotspot"
              onClick={() => router.push(h.route)}
              style={{
                position: "absolute",
                left: h.left,
                top: h.top,
                width: h.width,
                height: h.height,
                cursor: "pointer",
                borderRadius: 18,
              }}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
