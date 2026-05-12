"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const ArrowRight = ({ size = 12, color = "white" }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M5 12h14M13 5l7 7-7 7" />
  </svg>
);

const InfoIcon = ({ size = 11, color = "#9A9690" }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: "-1px", marginRight: 4 }} aria-hidden>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 16v-4M12 8h.01" />
  </svg>
);

interface Card {
  title: string;
  desc: React.ReactNode;
  route: string;
  preview: React.ReactNode;
}

const PreviewImage = ({ src, alt }: { src: string; alt: string }) => (
  <div style={{ aspectRatio: "440/500", marginBottom: 12, borderRadius: 8, overflow: "hidden", background: "#F5F2EC" }}>
    <img src={src} alt={alt} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
  </div>
);

const Card1Preview = () => <PreviewImage src="/reproduce-preview/card1.png" alt="分镜复刻预览" />;
const Card2Preview = () => <PreviewImage src="/reproduce-preview/card2.png" alt="图片复刻预览" />;
const Card3Preview = () => <PreviewImage src="/reproduce-preview/card3.png" alt="视频复刻预览" />;

const CARDS: Card[] = [
  {
    title: "分镜复刻",
    desc: <>视频拆成 9 宫格<br />替换元素重新出片</>,
    route: "/video/frame-extract",
    preview: <Card1Preview />,
  },
  {
    title: "图片复刻",
    desc: <>产品图 + 模特<br />AI 出脚本 + 拍片</>,
    route: "/video/general",
    preview: <Card2Preview />,
  },
  {
    title: "视频复刻",
    desc: <>参考视频一键换产品<br />Seedance r2v</>,
    route: "/video-clone-v2",
    preview: <Card3Preview />,
  },
];

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        .reproCard {
          background: white;
          border-radius: 16px;
          padding: 12px;
          border: 0.5px solid rgba(0,0,0,0.06);
          cursor: pointer;
          transition: transform 0.25s cubic-bezier(0.2,0.9,0.3,1.2), box-shadow 0.25s, border-color 0.25s;
        }
        .reproCard:hover {
          transform: translateY(-4px);
          box-shadow: 0 16px 38px rgba(0,0,0,0.1);
          border-color: rgba(0,0,0,0.12);
        }
        .reproCard:hover .arrowCircle {
          transform: rotate(-45deg);
        }
        .arrowCircle {
          width: 22px;
          height: 22px;
          border-radius: 50%;
          background: #2C2C2A;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: transform 0.25s;
        }
      `}</style>
      <Sidebar />
      <main style={{ flex: 1, padding: "2.5rem 3rem", overflowY: "auto", maxWidth: "1280px", width: "100%", margin: "0 auto" }}>
        <div style={{ background: "#EFEBE3", borderRadius: 20, padding: "24px 20px" }}>
          <div style={{ fontSize: 11, color: "#9A9690", marginBottom: 4, letterSpacing: "0.3px" }}>
            分镜复刻 · 图片复刻 · 视频复刻
          </div>
          <h2 style={{ fontSize: 22, fontWeight: 500, margin: "0 0 20px", color: "#2C2C2A" }}>视频复刻</h2>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {CARDS.map((c) => (
              <div key={c.title} className="reproCard" onClick={() => router.push(c.route)}>
                {c.preview}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                  <div style={{ fontSize: 14, fontWeight: 500, color: "#2C2C2A" }}>{c.title}</div>
                  <div className="arrowCircle">
                    <ArrowRight size={12} color="white" />
                  </div>
                </div>
                <div style={{ fontSize: 11, color: "#888", lineHeight: 1.5 }}>{c.desc}</div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 16, fontSize: 11, color: "#9A9690", textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <InfoIcon />
            <span>每张卡片的预览图未来可换成真实生成案例</span>
          </div>
        </div>
      </main>
    </div>
  );
}
