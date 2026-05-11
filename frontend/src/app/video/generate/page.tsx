"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

interface SubCard {
  title: string;
  desc: string;
  route: string;
  gradient: string;
}

const SUBS: SubCard[] = [
  {
    title: "图生视频",
    desc: "上传一张图 · 写提示词 · AI 拍 5-10s 短片",
    route: "/video",
    gradient: "linear-gradient(135deg,#ff0844 0%,#ff5858 30%,#f857a6 65%,#ffe53b 100%)",
  },
  {
    title: "元素替换",
    desc: "上传视频 + 替换图 · 自定义指令 · 局部换",
    route: "/video/replace",
    gradient: "linear-gradient(135deg,#0061ff 0%,#4facfe 30%,#00f2fe 65%,#43e97b 100%)",
  },
  {
    title: "翻拍",
    desc: "拆帧 storyboard · 整段重拍 · 多图参考出片",
    route: "/video/frame-extract",
    gradient: "linear-gradient(135deg,#fa709a 0%,#ee9ca7 30%,#fee140 65%,#c471f5 100%)",
  },
];

export default function VideoGenerateHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        @keyframes gradShift {
          0% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
          100% { background-position: 0% 50%; }
        }
        .hubCard {
          background-size: 220% 220%;
          animation: gradShift 8s ease infinite;
          transition: transform 0.3s cubic-bezier(0.2,0.9,0.3,1.2), box-shadow 0.3s, filter 0.3s;
          position: relative;
          overflow: hidden;
        }
        .hubCard:hover {
          transform: translateY(-8px) scale(1.02);
          box-shadow: 0 28px 70px rgba(0,0,0,0.35);
          filter: saturate(1.2) brightness(1.06);
          animation-duration: 4s;
        }
        .hubCard::before {
          content: "";
          position: absolute;
          top: 0; left: 0;
          width: 60%; height: 200%;
          background: linear-gradient(110deg, transparent 30%, rgba(255,255,255,0.3) 50%, transparent 70%);
          transform: translateX(-120%) skewX(-20deg);
          pointer-events: none;
          opacity: 0;
        }
        .hubCard:hover::before {
          opacity: 1;
          animation: shineHub 1s ease-out;
        }
        @keyframes shineHub {
          0% { transform: translateX(-120%) skewX(-20deg); }
          100% { transform: translateX(220%) skewX(-20deg); }
        }
      `}</style>
      <Sidebar />
      <main style={{ flex: 1, padding: "3rem 4rem", overflowY: "auto", maxWidth: "1280px", width: "100%", margin: "0 auto" }}>
        <div style={{ marginBottom: "2.5rem" }}>
          <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>图生视频 · 元素替换 · 翻拍</div>
          <h1 style={{ fontSize: "2rem", fontWeight: 300, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>视频生成</h1>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: "1.4rem" }}>
          {SUBS.map((c) => (
            <div
              key={c.title}
              className="hubCard"
              onClick={() => router.push(c.route)}
              style={{
                background: c.gradient,
                borderRadius: "22px",
                aspectRatio: "3/4",
                padding: "1.6rem",
                cursor: "pointer",
                display: "flex",
                flexDirection: "column",
                justifyContent: "flex-end",
                boxShadow: "0 18px 50px rgba(0,0,0,0.22)",
              }}
            >
              <div style={{ fontSize: "1.4rem", fontWeight: 600, color: "#fff", marginBottom: "0.3rem", textShadow: "0 2px 10px rgba(0,0,0,0.25)", position: "relative", zIndex: 2 }}>
                {c.title}
              </div>
              <div style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.95)", lineHeight: 1.5, textShadow: "0 1px 5px rgba(0,0,0,0.25)", position: "relative", zIndex: 2 }}>
                {c.desc}
              </div>
              <div style={{ position: "absolute", bottom: "1.4rem", right: "1.4rem", width: "36px", height: "36px", borderRadius: "50%", background: "rgba(255,255,255,0.92)", color: "#0d0d0d", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "1rem", fontWeight: 500, zIndex: 2 }}>
                →
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
