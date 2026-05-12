"use client";
import { useState, useEffect, useCallback } from "react";

interface Slide {
  img: string;
  title: string;
  subtitle: string;
}

const SLIDES: Slide[] = [
  { img: "/dashboard/carousel/44.webp",  title: "油画梦境花园", subtitle: "穿过油画拱门,梦门通向与现境的花园,无声起步" },
  { img: "/dashboard/carousel/105.webp", title: "藤蔓拱门花园", subtitle: "藤蔓缠绕的拱门通往远方花海,光影斑驳" },
  { img: "/dashboard/carousel/002.webp", title: "海边小镇", subtitle: "灯塔守望海岸,海鸟与花径同行" },
  { img: "/dashboard/carousel/06.webp",  title: "森林少女", subtitle: "落叶为幕,光斑流转的肖像油画" },
  { img: "/dashboard/carousel/04.webp",  title: "湖畔黄昏", subtitle: "落日染金水面,帆影点点的暖调写生" },
  { img: "/dashboard/carousel/100.webp", title: "田园小屋", subtitle: "水彩笔触下的乡村花园与清晨" },
];

export default function DashboardCarousel() {
  const [idx, setIdx] = useState(0);
  const n = SLIDES.length;

  const go = useCallback((delta: number) => {
    setIdx((i) => (i + delta + n) % n);
  }, [n]);

  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % n), 5000);
    return () => clearInterval(t);
  }, [n]);

  const cur = SLIDES[idx];
  return (
    <div className="carouselWrap">
      <style jsx>{`
        .carouselWrap {
          width: 100%;
          margin-bottom: 14px;
        }
        .banner {
          position: relative;
          width: 100%;
          aspect-ratio: 1200 / 460;
          border-radius: 18px;
          overflow: hidden;
          background: #d8d2c4;
        }
        .banner img.bannerImg {
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          object-fit: cover;
          transition: opacity 0.6s ease;
        }
        .overlay {
          position: absolute;
          left: 26px;
          bottom: 22px;
          max-width: 360px;
          padding: 16px 20px;
          background: rgba(255, 255, 255, 0.86);
          backdrop-filter: blur(8px);
          border-radius: 14px;
          z-index: 2;
        }
        .overlayTag {
          font-size: 11px;
          color: #9A9690;
          letter-spacing: 0.4px;
          margin-bottom: 6px;
        }
        .overlayTitle {
          font-size: 22px;
          font-weight: 500;
          color: #2C2C2A;
          margin: 0 0 6px;
          letter-spacing: 0.4px;
        }
        .overlaySub {
          font-size: 12px;
          color: #6E6A63;
          line-height: 1.55;
          margin: 0 0 12px;
        }
        .overlayBtn {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 7px 14px;
          background: #2C2C2A;
          color: #fff;
          font-size: 12px;
          border: none;
          border-radius: 999px;
          cursor: pointer;
          letter-spacing: 0.4px;
        }
        .nav {
          position: absolute;
          top: 50%;
          transform: translateY(-50%);
          width: 36px;
          height: 36px;
          border-radius: 50%;
          background: rgba(255, 255, 255, 0.7);
          border: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 3;
          transition: background 0.2s;
        }
        .nav:hover { background: rgba(255, 255, 255, 0.95); }
        .nav.prev { left: 14px; }
        .nav.next { right: 14px; }
        .dots {
          display: flex;
          justify-content: center;
          align-items: center;
          gap: 8px;
          margin-top: 14px;
        }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: rgba(44, 44, 42, 0.22);
          border: none;
          padding: 0;
          cursor: pointer;
          transition: background 0.2s, width 0.2s;
        }
        .dot.active {
          background: #2C2C2A;
          width: 22px;
          border-radius: 999px;
        }
      `}</style>
      <div className="banner">
        {SLIDES.map((s, i) => (
          <img
            key={s.img}
            className="bannerImg"
            src={s.img}
            alt={s.title}
            width={900}
            height={345}
            loading={i === 0 ? "eager" : "lazy"}
            fetchPriority={i === 0 ? "high" : "low"}
            decoding="async"
            draggable={false}
            style={{ opacity: i === idx ? 1 : 0 }}
          />
        ))}
        <div className="overlay">
          <div className="overlayTag">{`#${String(idx + 1).padStart(2, "0")} 精选场景模板`}</div>
          <h3 className="overlayTitle">{cur.title}</h3>
          <p className="overlaySub">{cur.subtitle}</p>
        </div>
        <button className="nav prev" type="button" onClick={() => go(-1)} aria-label="上一张">
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#2C2C2A" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 18l-6-6 6-6" />
          </svg>
        </button>
        <button className="nav next" type="button" onClick={() => go(1)} aria-label="下一张">
          <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="#2C2C2A" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </button>
      </div>
      <div className="dots">
        {SLIDES.map((s, i) => (
          <button
            key={s.img}
            type="button"
            className={`dot${i === idx ? " active" : ""}`}
            onClick={() => setIdx(i)}
            aria-label={`第 ${i + 1} 张 ${s.title}`}
          />
        ))}
      </div>
    </div>
  );
}
