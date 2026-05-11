"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const PlayIcon = ({ size = 14, color = "white", opacity = 0.9 }: { size?: number; color?: string; opacity?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={color} style={{ opacity }} aria-hidden>
    <path d="M8 5v14l11-7z" />
  </svg>
);

const ArrowRight = ({ size = 12, color = "white" }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M5 12h14M13 5l7 7-7 7" />
  </svg>
);

const ArrowDown = ({ size = 12, color = "#888" }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 5v14M5 13l7 7 7-7" />
  </svg>
);

const UserIcon = ({ size = 28, color = "rgba(255,255,255,0.85)" }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 22c0-4.4 3.6-8 8-8s8 3.6 8 8" />
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

const Card1Preview = () => (
  <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 3, aspectRatio: "1.1", marginBottom: 12, background: "#F5F2EC", padding: 4, borderRadius: 8 }}>
    <div style={{ background: "linear-gradient(135deg,#F0997B,#D4537E)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#FAC775,#EF9F27)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#AFA9EC,#7F77DD)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#5DCAA5,#1D9E75)", borderRadius: 3, position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <PlayIcon size={14} />
    </div>
    <div style={{ background: "linear-gradient(135deg,#85B7EB,#378ADD)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#ED93B1,#D4537E)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#B5D4F4,#85B7EB)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#F4C0D1,#ED93B1)", borderRadius: 3 }} />
    <div style={{ background: "linear-gradient(135deg,#FAEEDA,#FAC775)", borderRadius: 3 }} />
  </div>
);

const Card2Preview = () => (
  <div style={{ aspectRatio: "1.1", marginBottom: 12, background: "#F5F2EC", padding: 8, borderRadius: 8, position: "relative", display: "flex", flexDirection: "column" }}>
    <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
      <div style={{ flex: 1, aspectRatio: 1, background: "linear-gradient(135deg,#FBEAF0,#ED93B1)", borderRadius: 4 }} />
      <div style={{ flex: 1, aspectRatio: 1, background: "linear-gradient(135deg,#E6F1FB,#85B7EB)", borderRadius: 4 }} />
      <div style={{ flex: 1, aspectRatio: 1, background: "linear-gradient(135deg,#FAEEDA,#FAC775)", borderRadius: 4 }} />
    </div>
    <div style={{ textAlign: "center", margin: "4px 0", lineHeight: 0 }}>
      <ArrowDown />
    </div>
    <div style={{ background: "linear-gradient(135deg,#534AB7,#7F77DD)", flex: 1, minHeight: 60, borderRadius: 4, position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <UserIcon />
      <div style={{ position: "absolute", bottom: 4, right: 4, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <PlayIcon size={12} />
      </div>
    </div>
  </div>
);

const Card3Preview = () => (
  <div style={{ aspectRatio: "1.1", marginBottom: 12, background: "#F5F2EC", padding: 8, borderRadius: 8, display: "flex", flexDirection: "column", gap: 4 }}>
    <div style={{ flex: 1, background: "linear-gradient(135deg,#888780,#5F5E5A)", borderRadius: 4, position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <PlayIcon size={18} />
      <div style={{ position: "absolute", top: 3, left: 5, fontSize: 8, color: "white", background: "rgba(0,0,0,0.4)", padding: "1px 5px", borderRadius: 3 }}>参考</div>
    </div>
    <div style={{ textAlign: "center", lineHeight: 0 }}>
      <ArrowDown size={11} />
    </div>
    <div style={{ flex: 1, background: "linear-gradient(135deg,#1D9E75,#0F6E56)", borderRadius: 4, position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <PlayIcon size={18} />
      <div style={{ position: "absolute", top: 3, left: 5, fontSize: 8, color: "white", background: "rgba(0,0,0,0.4)", padding: "1px 5px", borderRadius: 3 }}>替换后</div>
    </div>
  </div>
);

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
