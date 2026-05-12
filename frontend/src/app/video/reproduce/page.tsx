"use client";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const NINE_CELLS = Array.from({ length: 9 }, (_, i) => `/reproduce-preview/nine/cell${i + 1}.png`);

const ArrowDown = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="#9A9690" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 5v14M5 13l7 7 7-7" />
  </svg>
);

type Preview =
  | { kind: "nine-grid"; cells: string[] }
  | { kind: "stack-3-1"; smalls: string[]; large: string }
  | { kind: "stack-2"; cells: { img: string; tag: string }[] }
  | { kind: "composite"; src: string };

interface CardDef {
  title: string;
  desc: string[];
  route: string;
  preview: Preview;
  aspect: string; // CSS aspect-ratio,各卡不同
}

const CARDS: CardDef[] = [
  {
    title: "分镜复刻",
    desc: ["视频拆成 9 宫格", "替换元素重新出片"],
    route: "/video/frame-extract",
    preview: { kind: "composite", src: "/reproduce-preview/card1.png" },
    aspect: "420 / 480",
  },
  {
    title: "图片复刻",
    desc: ["产品图 + 模特", "AI 出脚本 + 拍片"],
    route: "/video/general",
    preview: {
      kind: "stack-3-1",
      smalls: [
        "/reproduce-preview/card2/small1.png",
        "/reproduce-preview/card2/small2.png",
        "/reproduce-preview/card2/small3.png",
      ],
      large: "/reproduce-preview/card2/large.png",
    },
    aspect: "3 / 4",
  },
  {
    title: "视频复刻",
    desc: ["参考视频一键换产品", "Seedance r2v"],
    route: "/video-clone-v2",
    preview: {
      kind: "stack-2",
      cells: [
        { img: "/reproduce-preview/card3/top.png", tag: "参考" },
        { img: "/reproduce-preview/card3/bottom.png", tag: "替换后" },
      ],
    },
    aspect: "4 / 5",
  },
];

const PlayIcon = () => (
  <svg width={26} height={26} viewBox="0 0 24 24" aria-hidden>
    <circle cx="12" cy="12" r="11" fill="rgba(255,255,255,0.85)" />
    <path d="M10 8.5v7l5.5-3.5z" fill="#2C2C2A" />
  </svg>
);

export default function VideoReproduceHub() {
  const router = useRouter();
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <style jsx>{`
        .fullcardWrap {
          cursor: pointer;
          display: block;
          width: 100%;
          border-radius: 18px;
          overflow: hidden;
          transition: transform 0.32s cubic-bezier(0.2, 0.9, 0.3, 1.2),
                      box-shadow 0.32s ease;
          will-change: transform;
        }
        .fullcardWrap img {
          width: 100%;
          height: auto;
          display: block;
        }
        .fullcardWrap:hover {
          transform: translateY(-10px);
          box-shadow: 0 24px 50px rgba(0, 0, 0, 0.14),
                      0 6px 14px rgba(0, 0, 0, 0.06);
        }
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
          min-width: 0;
          overflow: hidden;
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
          /* aspect-ratio 由每张卡自行 inline 覆盖:1/1 / 3/4 / 4/5 */
          overflow: hidden;
          border-radius: 10px;
          margin-bottom: 14px;
          background: transparent;
          padding: 0;
          box-sizing: border-box;
        }
        .preview.framed {
          background: #F5F2EC;
          padding: 6px;
        }
        .preview img {
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
        }
        .preview .composite-fill {
          width: 100%;
          height: 100%;
          object-fit: cover;
          object-position: center;
          display: block;
        }

        /* 9 宫格(规范 6) */
        .nine-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          grid-template-rows: repeat(3, minmax(0, 1fr));
          gap: 4px;
          width: 100%;
          height: 100%;
          min-height: 0;
          min-width: 0;
        }
        .nine-grid img { border-radius: 3px; }

        /* 卡 2:3 小 + 大(规范 7 flex column,每 row flex:1 + min-height:0) */
        .stack-3-1 {
          display: flex;
          flex-direction: column;
          gap: 6px;
          width: 100%;
          height: 100%;
        }
        .stack-3-1 .row3 {
          display: flex;
          gap: 6px;
          flex: 1;
          min-height: 0;
        }
        .stack-3-1 .row3 .cell {
          flex: 1;
          min-width: 0;
          overflow: hidden;
          border-radius: 4px;
        }
        .stack-3-1 .arrowRow {
          display: flex;
          justify-content: center;
          align-items: center;
          flex: 0 0 auto;
          height: 12px;
        }
        .stack-3-1 .large {
          flex: 2;
          min-height: 0;
          overflow: hidden;
          border-radius: 4px;
        }

        /* 卡 3:2 视频堆叠(规范 7 flex column + 每 cell flex:1 + min-height:0) */
        .stack-2 {
          display: flex;
          flex-direction: column;
          gap: 6px;
          width: 100%;
          height: 100%;
        }
        .stack-2 .videoCell {
          flex: 1;
          min-height: 0;
          position: relative;
          overflow: hidden;
          border-radius: 4px;
        }
        .stack-2 .videoCell .tag {
          position: absolute;
          top: 8px;
          left: 8px;
          font-size: 11px;
          color: #fff;
          background: rgba(0, 0, 0, 0.65);
          padding: 2px 8px;
          border-radius: 4px;
          z-index: 2;
        }
        .stack-2 .videoCell .play {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          z-index: 2;
        }
        .stack-2 .arrowRow {
          display: flex;
          justify-content: center;
          align-items: center;
          flex: 0 0 auto;
          height: 12px;
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
          minWidth: 0,
        }}
      >
        <div style={{ background: "#EFEBE3", borderRadius: 22, padding: "26px 22px" }}>
          <div style={{ fontSize: 11, color: "#9A9690", marginBottom: 6, letterSpacing: "0.4px" }}>
            分镜复刻 · 图片复刻 · 视频复刻
          </div>
          <h2 style={{ fontSize: 24, fontWeight: 500, margin: "0 0 22px", color: "#2C2C2A", letterSpacing: "0.5px" }}>
            视频复刻
          </h2>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
              alignItems: "center",
              gap: 22,
            }}
          >
            {CARDS.map((c) => {
              return (
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
                <div className="preview" style={{ aspectRatio: c.aspect }}>
                  {c.preview.kind === "composite" && (
                    <img className="composite-fill" src={c.preview.src} alt="" draggable={false} />
                  )}
                  {c.preview.kind === "nine-grid" && (
                    <div className="nine-grid">
                      {c.preview.cells.map((src, i) => (
                        <img key={i} src={src} alt="" draggable={false} />
                      ))}
                    </div>
                  )}
                  {c.preview.kind === "stack-3-1" && (
                    <div className="stack-3-1">
                      <div className="row3">
                        {c.preview.smalls.map((src, i) => (
                          <div key={i} className="cell">
                            <img src={src} alt="" draggable={false} />
                          </div>
                        ))}
                      </div>
                      <div className="arrowRow"><ArrowDown /></div>
                      <div className="large">
                        <img src={c.preview.large} alt="" draggable={false} />
                      </div>
                    </div>
                  )}
                  {c.preview.kind === "stack-2" && (
                    <div className="stack-2">
                      {c.preview.cells.map((cell, i) => (
                        <>
                          {i === 1 && <div key={`arr-${i}`} className="arrowRow"><ArrowDown /></div>}
                          <div key={i} className="videoCell">
                            <span className="tag">{cell.tag}</span>
                            <img src={cell.img} alt="" draggable={false} />
                            <span className="play"><PlayIcon /></span>
                          </div>
                        </>
                      ))}
                    </div>
                  )}
                </div>
                <div className="titleRow">
                  <span className="title">{c.title}</span>
                  <div className="arrowCircle" aria-hidden>
                    <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
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
              );
            })}
          </div>

          <div style={{ marginTop: 18, fontSize: 11, color: "#9A9690", textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center", gap: 4 }}>
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
