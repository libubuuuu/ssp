"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

interface CardDef {
  title: string;
  desc: string[];
  route: string;
  img: string;
  alt: string;
}

const CARDS: CardDef[] = [
  {
    title: "分镜复刻",
    desc: ["视频拆成 9 宫格", "替换元素重新出片"],
    route: "/video/frame-extract",
    img: "/reproduce-preview/card1.png",
    alt: "分镜复刻预览",
  },
  {
    title: "图片复刻",
    desc: ["产品图 + 模特", "AI 出脚本 + 拍片"],
    route: "/video/general",
    img: "/reproduce-preview/card2.png",
    alt: "图片复刻预览",
  },
  {
    title: "视频复刻",
    desc: ["参考视频一键换产品", "Seedance r2v"],
    route: "/video-clone-v2",
    img: "/reproduce-preview/card3.png",
    alt: "视频复刻预览",
  },
];

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        .reproCard {
          background: #ffffff;
          border-radius: 18px;
          padding: 14px;
          cursor: pointer;
          border: 0.5px solid rgba(0, 0, 0, 0.04);
          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
          transition: transform 0.32s cubic-bezier(0.2, 0.9, 0.3, 1.2),
                      box-shadow 0.32s ease,
                      border-color 0.32s ease;
          will-change: transform;
          display: flex;
          flex-direction: column;
        }
        .reproCard:hover {
          transform: translateY(-10px);
          box-shadow: 0 24px 50px rgba(0, 0, 0, 0.14),
                      0 6px 14px rgba(0, 0, 0, 0.06);
          border-color: rgba(0, 0, 0, 0.08);
        }
        .reproCard:active {
          transform: translateY(-4px);
          box-shadow: 0 12px 26px rgba(0, 0, 0, 0.10);
        }
        .reproCard:hover .arrowCircle {
          transform: rotate(-45deg);
          background: #1a1a18;
        }

        .preview {
          width: 100%;
          aspect-ratio: 420 / 480;
          border-radius: 10px;
          overflow: hidden;
          margin-bottom: 14px;
          background: #F5F2EC;
        }
        .preview img {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
          user-select: none;
          -webkit-user-drag: none;
        }

        .titleRow {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 6px;
        }
        .title {
          font-size: 15px;
          font-weight: 600;
          color: #2C2C2A;
          letter-spacing: 0.2px;
        }
        .arrowCircle {
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background: #2C2C2A;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: transform 0.28s, background 0.28s;
          flex-shrink: 0;
        }
        .arrowCircle svg {
          width: 12px;
          height: 12px;
        }

        .desc {
          font-size: 12px;
          color: #888888;
          line-height: 1.6;
          margin: 0;
        }
        .desc span { display: block; }
      `}</style>
      <Sidebar />
      <main
        style={{
          flex: 1,
          padding: "2.5rem 3rem",
          overflowY: "auto",
          maxWidth: 1280,
          width: "100%",
          margin: "0 auto",
        }}
      >
        <div style={{ background: "#EFEBE3", borderRadius: 22, padding: "26px 22px" }}>
          <div
            style={{
              fontSize: 11,
              color: "#9A9690",
              marginBottom: 6,
              letterSpacing: "0.4px",
            }}
          >
            分镜复刻 · 图片复刻 · 视频复刻
          </div>
          <h2
            style={{
              fontSize: 24,
              fontWeight: 500,
              margin: "0 0 22px",
              color: "#2C2C2A",
              letterSpacing: "0.5px",
            }}
          >
            视频复刻
          </h2>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18 }}>
            {CARDS.map((c) => (
              <div
                key={c.route}
                className="reproCard"
                onClick={() => router.push(c.route)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") router.push(c.route);
                }}
              >
                <div className="preview">
                  <img src={c.img} alt={c.alt} draggable={false} />
                </div>
                <div className="titleRow">
                  <span className="title">{c.title}</span>
                  <div className="arrowCircle" aria-hidden>
                    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 12h14M13 5l7 7-7 7" />
                    </svg>
                  </div>
                </div>
                <p className="desc">
                  {c.desc.map((line, i) => (
                    <span key={i}>{line}</span>
                  ))}
                </p>
              </div>
            ))}
          </div>

          <div
            style={{
              marginTop: 18,
              fontSize: 11,
              color: "#9A9690",
              textAlign: "center",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 4,
            }}
          >
            <svg width={11} height={11} viewBox="0 0 24 24" fill="none" stroke="#9A9690" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="12" cy="12" r="10" />
              <path d="M12 16v-4M12 8h.01" />
            </svg>
            <span>每张卡片均可点击,真实预览图来自示例案例</span>
          </div>
        </div>
      </main>
    </div>
  );
}
