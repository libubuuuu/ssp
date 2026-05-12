"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

// 整页用 5 张图拼:topbar + 3 张完整卡 + bottombar
// 每张卡 div 独立,hover 时整张弹起,卡内一切文字/箭头/预览都在图里 -> 像素与截图 100% 对齐
const CARDS = [
  { route: "/video/frame-extract", img: "/reproduce-preview/fullcard1.png" },
  { route: "/video/general",       img: "/reproduce-preview/fullcard2.png" },
  { route: "/video-clone-v2",      img: "/reproduce-preview/fullcard3.png" },
];

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        .liftCard {
          cursor: pointer;
          border-radius: 16px;
          overflow: hidden;
          transition: transform 0.32s cubic-bezier(0.2,0.9,0.3,1.2), box-shadow 0.32s ease;
          will-change: transform;
          background: transparent;
        }
        .liftCard img {
          width: 100%;
          height: auto;
          display: block;
          user-select: none;
          -webkit-user-drag: none;
        }
        .liftCard:hover {
          transform: translateY(-10px);
          box-shadow: 0 24px 50px rgba(0,0,0,0.14), 0 6px 14px rgba(0,0,0,0.06);
        }
        .liftCard:active {
          transform: translateY(-4px);
          box-shadow: 0 12px 26px rgba(0,0,0,0.10);
        }
      `}</style>
      <Sidebar />
      <main style={{ flex: 1, overflowY: "auto", display: "flex", justifyContent: "center", padding: "1.5rem" }}>
        <div style={{ width: "100%", maxWidth: 1467, display: "flex", flexDirection: "column" }}>
          {/* topbar:小副标题 + "视频复刻" 标题 */}
          <img
            src="/reproduce-preview/topbar.png"
            alt=""
            style={{ width: "100%", height: "auto", display: "block", userSelect: "none" }}
            draggable={false}
          />
          {/* 3 张完整卡,grid 排列 */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "1%", padding: "0.5% 0" }}>
            {CARDS.map((c) => (
              <div key={c.route} className="liftCard" onClick={() => router.push(c.route)}>
                <img src={c.img} alt="" draggable={false} />
              </div>
            ))}
          </div>
          {/* bottombar:info 文字 */}
          <img
            src="/reproduce-preview/bottombar.png"
            alt=""
            style={{ width: "100%", height: "auto", display: "block", userSelect: "none" }}
            draggable={false}
          />
        </div>
      </main>
    </div>
  );
}
