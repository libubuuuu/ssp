"use client";
import { useRouter } from "next/navigation";

interface Card {
  title: string;
  desc: string;
  route: string;
  gradient: string;
}

const CARDS: Card[] = [
  {
    title: "图片生成",
    desc: "电商主图 · 模特图 · 多图参考",
    route: "/image",
    gradient: "linear-gradient(135deg,#ff5858 0%,#f857a6 50%,#ffb86c 100%)",
  },
  {
    title: "视频复刻",
    desc: "上传参考视频 · 一键产品替换",
    route: "/video-clone-v2",
    gradient: "linear-gradient(135deg,#4facfe 0%,#00f2fe 50%,#a18cd1 100%)",
  },
  {
    title: "视频生成",
    desc: "拆帧 storyboard · Seedance 出片",
    route: "/video/frame-extract",
    gradient: "linear-gradient(135deg,#fa709a 0%,#fee140 50%,#a18cd1 100%)",
  },
];

interface Props {
  mode: "popup" | "embedded";
  onClose?: () => void;
}

export default function FeatureShowcase({ mode, onClose }: Props) {
  const router = useRouter();
  const handlePick = (route: string) => {
    if (mode === "popup" && onClose) onClose();
    router.push(route);
  };

  if (mode === "popup") {
    return (
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,15,15,0.55)",
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "2rem",
        }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            maxWidth: "1100px",
            width: "100%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}
        >
          <div
            style={{
              fontSize: "1.8rem",
              fontFamily: "Georgia,serif",
              fontStyle: "italic",
              color: "#fff",
              marginBottom: "0.6rem",
              letterSpacing: "0.02em",
            }}
          >
            xiaoLi ai.
          </div>
          <div
            style={{
              fontSize: "1.6rem",
              color: "#fff",
              marginBottom: "2.5rem",
              fontWeight: 300,
              letterSpacing: "0.05em",
            }}
          >
            一键生图工作室
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3,1fr)",
              gap: "1.4rem",
              width: "100%",
            }}
          >
            {CARDS.map((c) => (
              <div
                key={c.title}
                onClick={() => handlePick(c.route)}
                style={{
                  background: c.gradient,
                  borderRadius: "24px",
                  aspectRatio: "3/4",
                  padding: "1.6rem",
                  cursor: "pointer",
                  position: "relative",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "flex-end",
                  boxShadow: "0 20px 60px rgba(0,0,0,0.35)",
                  transition: "transform 0.25s, box-shadow 0.25s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = "translateY(-6px)";
                  e.currentTarget.style.boxShadow = "0 28px 80px rgba(0,0,0,0.45)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = "translateY(0)";
                  e.currentTarget.style.boxShadow = "0 20px 60px rgba(0,0,0,0.35)";
                }}
              >
                <div
                  style={{
                    fontSize: "1.45rem",
                    fontWeight: 600,
                    color: "#fff",
                    marginBottom: "0.3rem",
                    textShadow: "0 2px 8px rgba(0,0,0,0.25)",
                  }}
                >
                  {c.title}
                </div>
                <div
                  style={{
                    fontSize: "0.82rem",
                    color: "rgba(255,255,255,0.92)",
                    lineHeight: 1.5,
                    textShadow: "0 1px 4px rgba(0,0,0,0.2)",
                  }}
                >
                  {c.desc}
                </div>
                <div
                  style={{
                    position: "absolute",
                    bottom: "1.4rem",
                    right: "1.4rem",
                    width: "36px",
                    height: "36px",
                    borderRadius: "50%",
                    background: "rgba(255,255,255,0.95)",
                    color: "#0d0d0d",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "1rem",
                    fontWeight: 500,
                  }}
                >
                  →
                </div>
              </div>
            ))}
          </div>
          <button
            onClick={onClose}
            style={{
              marginTop: "2rem",
              background: "transparent",
              border: "1px solid rgba(255,255,255,0.4)",
              color: "rgba(255,255,255,0.85)",
              padding: "0.55rem 1.6rem",
              borderRadius: "999px",
              cursor: "pointer",
              fontSize: "0.85rem",
              letterSpacing: "0.05em",
            }}
          >
            稍后再看
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(3,1fr)",
        gap: "1.2rem",
        marginBottom: "2.5rem",
      }}
    >
      {CARDS.map((c) => (
        <div
          key={c.title}
          onClick={() => handlePick(c.route)}
          style={{
            background: "linear-gradient(135deg,#1a1a1a 0%,#0d0d0d 50%,#161616 100%)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: "20px",
            aspectRatio: "16/10",
            padding: "1.6rem",
            cursor: "pointer",
            position: "relative",
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-end",
            overflow: "hidden",
            transition: "transform 0.25s, border-color 0.25s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = "translateY(-3px)";
            e.currentTarget.style.borderColor = "rgba(255,255,255,0.18)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = "translateY(0)";
            e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)";
          }}
        >
          <div
            style={{
              position: "absolute",
              top: "-30%",
              right: "-20%",
              width: "70%",
              height: "120%",
              background: c.gradient,
              opacity: 0.32,
              filter: "blur(40px)",
              borderRadius: "50%",
              pointerEvents: "none",
            }}
          />
          <div
            style={{
              position: "relative",
              fontSize: "1.35rem",
              fontWeight: 500,
              color: "#fff",
              marginBottom: "0.35rem",
              fontFamily: "Georgia,serif",
              letterSpacing: "0.02em",
            }}
          >
            {c.title}
          </div>
          <div
            style={{
              position: "relative",
              fontSize: "0.82rem",
              color: "rgba(255,255,255,0.55)",
              lineHeight: 1.5,
            }}
          >
            {c.desc}
          </div>
          <div
            style={{
              position: "absolute",
              top: "1.4rem",
              right: "1.4rem",
              width: "32px",
              height: "32px",
              borderRadius: "50%",
              border: "1px solid rgba(255,255,255,0.2)",
              color: "rgba(255,255,255,0.85)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: "0.9rem",
            }}
          >
            →
          </div>
        </div>
      ))}
    </div>
  );
}
