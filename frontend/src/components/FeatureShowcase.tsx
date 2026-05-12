"use client";
import { useRouter } from "next/navigation";

interface Card {
  title: string;
  desc: string;
  route: string;
  // 多角度 conic-gradient,用 background-position 跑动画
  gradient: string;
  embeddedGlow: string;
  image?: string;
}

const CARDS: Card[] = [
  {
    title: "图片生成",
    desc: "电商主图 · 模特图 · 多图参考",
    route: "/image",
    gradient:
      "linear-gradient(135deg,#ff0844 0%,#ff5858 20%,#f857a6 45%,#ff9966 70%,#ffe53b 100%)",
    embeddedGlow:
      "linear-gradient(135deg,#ff0844 0%,#f857a6 50%,#ffe53b 100%)",
    image: "/dashboard/cards/image-gen.webp",
  },
  {
    title: "视频复刻",
    desc: "分镜复刻 · 图片复刻 · 视频复刻",
    route: "/video/reproduce",
    gradient:
      "linear-gradient(135deg,#0061ff 0%,#4facfe 25%,#00f2fe 50%,#43e97b 75%,#a18cd1 100%)",
    embeddedGlow:
      "linear-gradient(135deg,#0061ff 0%,#00f2fe 50%,#a18cd1 100%)",
    image: "/dashboard/cards/video-clone.webp",
  },
  {
    title: "视频生成",
    desc: "上传一张图 · 写提示词 · AI 拍 5-10s 短片",
    route: "/video",
    gradient:
      "linear-gradient(135deg,#fa709a 0%,#ee9ca7 25%,#fee140 50%,#c471f5 75%,#fa71cd 100%)",
    embeddedGlow:
      "linear-gradient(135deg,#fa709a 0%,#fee140 50%,#c471f5 100%)",
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
      <>
        <style jsx>{`
          @keyframes gradShift {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
          }
          @keyframes float {
            0%,100% { transform: translateY(0) rotate(0deg); }
            50% { transform: translateY(-6px) rotate(0.4deg); }
          }
          @keyframes popIn {
            from { opacity: 0; transform: scale(0.92) translateY(20px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
          }
          @keyframes shine {
            0% { transform: translateX(-120%) skewX(-20deg); }
            100% { transform: translateX(220%) skewX(-20deg); }
          }
          .popupCard {
            background-size: 220% 220%;
            animation: gradShift 7s ease infinite, float 5s ease-in-out infinite, popIn 0.55s cubic-bezier(0.2,0.9,0.3,1.2) both;
            transition: transform 0.3s cubic-bezier(0.2,0.9,0.3,1.2), box-shadow 0.3s, filter 0.3s;
            position: relative;
            overflow: hidden;
          }
          .popupCard:nth-child(2) { animation-delay: 0s, 0.6s, 0.08s; }
          .popupCard:nth-child(3) { animation-delay: 0s, 1.2s, 0.16s; }
          .popupCard:hover {
            transform: translateY(-12px) scale(1.04) !important;
            box-shadow: 0 38px 90px rgba(0,0,0,0.55), 0 0 60px rgba(255,255,255,0.15) !important;
            filter: saturate(1.25) brightness(1.08);
            animation-duration: 3s, 5s, 0.55s;
          }
          .popupCard::before {
            content: "";
            position: absolute;
            top: 0; left: 0;
            width: 60%; height: 200%;
            background: linear-gradient(110deg, transparent 30%, rgba(255,255,255,0.35) 50%, transparent 70%);
            transform: translateX(-120%) skewX(-20deg);
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.2s;
          }
          .popupCard:hover::before {
            opacity: 1;
            animation: shine 1.1s ease-out;
          }
          .popupArrow {
            transition: transform 0.3s, background 0.3s;
          }
          .popupCard:hover .popupArrow {
            transform: translate(4px,-4px) scale(1.1);
            background: #fff !important;
          }
        `}</style>
        <div
          onClick={onClose}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(8,8,12,0.62)",
            backdropFilter: "blur(10px)",
            WebkitBackdropFilter: "blur(10px)",
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
                  className="popupCard"
                  onClick={() => handlePick(c.route)}
                  style={{
                    background: c.gradient,
                    borderRadius: "24px",
                    aspectRatio: "3/4",
                    padding: "1.6rem",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "flex-end",
                    boxShadow: "0 20px 60px rgba(0,0,0,0.4)",
                  }}
                >
                  <div
                    style={{
                      fontSize: "1.45rem",
                      fontWeight: 600,
                      color: "#fff",
                      marginBottom: "0.3rem",
                      textShadow: "0 2px 12px rgba(0,0,0,0.35)",
                      position: "relative",
                      zIndex: 2,
                    }}
                  >
                    {c.title}
                  </div>
                  <div
                    style={{
                      fontSize: "0.82rem",
                      color: "rgba(255,255,255,0.95)",
                      lineHeight: 1.5,
                      textShadow: "0 1px 6px rgba(0,0,0,0.3)",
                      position: "relative",
                      zIndex: 2,
                    }}
                  >
                    {c.desc}
                  </div>
                  <div
                    className="popupArrow"
                    style={{
                      position: "absolute",
                      bottom: "1.4rem",
                      right: "1.4rem",
                      width: "36px",
                      height: "36px",
                      borderRadius: "50%",
                      background: "rgba(255,255,255,0.9)",
                      color: "#0d0d0d",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "1rem",
                      fontWeight: 500,
                      zIndex: 2,
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
      </>
    );
  }

  const handleMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    el.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  };

  return (
    <>
      <style jsx>{`
        @keyframes embedFlow {
          0% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
          100% { background-position: 0% 50%; }
        }
        @keyframes embedDrift {
          0%,100% { transform: translate(0,0) scale(1); }
          33% { transform: translate(8px,-6px) scale(1.04); }
          66% { transform: translate(-6px,8px) scale(0.98); }
        }
        .embedCard {
          background-size: 240% 240%;
          animation: embedFlow 9s ease infinite;
          --mx: 50%;
          --my: 50%;
          transition: transform 0.3s cubic-bezier(0.2,0.9,0.3,1.2), box-shadow 0.3s;
        }
        .embedCard:hover {
          transform: translateY(-6px) scale(1.015);
          box-shadow: 0 30px 70px rgba(0,0,0,0.28);
          animation-duration: 5s;
        }
        /* 流动的色块,会被鼠标"推开" */
        .embedBlob {
          position: absolute;
          width: 60%;
          height: 60%;
          border-radius: 50%;
          filter: blur(50px);
          pointer-events: none;
          animation: embedDrift 11s ease-in-out infinite;
          transition: transform 0.6s cubic-bezier(0.2,0.9,0.3,1.2);
        }
        .embedCard:hover .embedBlob {
          /* 用 mx/my 把色块往反方向推,做"躲避手指"效果 */
          transform: translate(calc((50% - var(--mx)) * 1.2), calc((50% - var(--my)) * 1.2));
        }
        /* 鼠标位置的高光"压痕" */
        .embedCard::after {
          content: "";
          position: absolute;
          inset: 0;
          pointer-events: none;
          background: radial-gradient(
            circle 220px at var(--mx) var(--my),
            rgba(255,255,255,0.32) 0%,
            rgba(255,255,255,0.1) 25%,
            transparent 55%
          );
          opacity: 0;
          transition: opacity 0.35s ease;
        }
        .embedCard:hover::after { opacity: 1; }
        .embedArrow {
          transition: transform 0.3s, background 0.3s, border-color 0.3s;
        }
        .embedCard:hover .embedArrow {
          background: rgba(255,255,255,0.95);
          color: #0d0d0d;
          border-color: transparent;
          transform: rotate(-45deg);
        }
      `}</style>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3,1fr)",
          gap: "1.6rem",
          marginBottom: "2.5rem",
        }}
      >
        {CARDS.map((c, i) => (
          <div
            key={c.title}
            className={`embedCard${c.image ? " hasImage" : ""}`}
            onClick={() => handlePick(c.route)}
            onMouseMove={handleMove}
            style={{
              background: c.image ? "#1a1a18" : c.gradient,
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: "24px",
              aspectRatio: "5/6",
              padding: "1.8rem",
              cursor: "pointer",
              position: "relative",
              display: "flex",
              flexDirection: "column",
              justifyContent: "flex-end",
              overflow: "hidden",
              boxShadow: "0 14px 40px rgba(0,0,0,0.18)",
            }}
          >
            {c.image ? (
              <div
                style={{
                  position: "absolute",
                  top: "1rem",
                  left: "1rem",
                  right: "1rem",
                  aspectRatio: "1448 / 1086",
                  borderRadius: "14px",
                  overflow: "hidden",
                  zIndex: 0,
                  background: "#1a1a18",
                }}
              >
                <img
                  src={c.image}
                  alt={c.title}
                  width={720}
                  height={540}
                  loading="eager"
                  decoding="async"
                  draggable={false}
                  style={{
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    display: "block",
                  }}
                />
              </div>
            ) : (
              <>
                <div
                  className="embedBlob"
                  style={{
                    top: i === 0 ? "10%" : i === 1 ? "20%" : "-5%",
                    left: i === 0 ? "10%" : i === 1 ? "50%" : "30%",
                    background: c.embeddedGlow,
                    opacity: 0.6,
                    animationDelay: `${i * 1.5}s`,
                  }}
                />
                <div
                  className="embedBlob"
                  style={{
                    bottom: "0%",
                    right: i === 0 ? "10%" : i === 1 ? "0%" : "20%",
                    background: c.gradient,
                    opacity: 0.45,
                    animationDelay: `${i * 1.5 + 4}s`,
                    animationDuration: "13s",
                  }}
                />
              </>
            )}
            <div
              style={{
                position: "relative",
                zIndex: 2,
                fontSize: "1.7rem",
                fontWeight: 600,
                color: "#fff",
                marginBottom: "0.5rem",
                textShadow: "0 2px 12px rgba(0,0,0,0.3)",
                letterSpacing: "0.02em",
              }}
            >
              {c.title}
            </div>
            <div
              style={{
                position: "relative",
                zIndex: 2,
                fontSize: "0.92rem",
                color: "rgba(255,255,255,0.92)",
                lineHeight: 1.55,
                textShadow: "0 1px 6px rgba(0,0,0,0.25)",
              }}
            >
              {c.desc}
            </div>
            <div
              className="embedArrow"
              style={{
                position: "absolute",
                top: "1.6rem",
                right: "1.6rem",
                width: "38px",
                height: "38px",
                borderRadius: "50%",
                border: "1px solid rgba(255,255,255,0.4)",
                background: "rgba(255,255,255,0.18)",
                backdropFilter: "blur(6px)",
                WebkitBackdropFilter: "blur(6px)",
                color: "#fff",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "1rem",
                zIndex: 3,
              }}
            >
              →
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
