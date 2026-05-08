"use client";
import { useEffect, useMemo, useRef, useState } from "react";

export interface MentionAsset {
  id: string;            // 稳定 id(删图重排不会错位)
  label: string;         // 占位符 "Video1" / "Image1"
  kind: "video" | "image";
  thumb: string;         // 缩略图 URL(video 同源直接当 thumb)
  filename: string;      // 原文件名
  meta?: string;         // 视频时长等附加信息
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  assets: MentionAsset[];
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  /** 提供按钮槽位(右上角 ✨ AI 帮我写)*/
  rightSlot?: React.ReactNode;
}

const RE_MENTION = /@(Video\d+|Image\d+)/g;

/**
 * 输入 @ 触发素材选择浮层。textarea 文字透明 + 同步 overlay 上色,实现 inline 蓝色 chip 视觉。
 * 提交时取 textarea.value(纯文本含 @Video1/@Image1)直接喂给后端。
 */
export default function MentionTextarea({ value, onChange, assets, placeholder, rows = 4, disabled, rightSlot }: Props) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const [popupOpen, setPopupOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const [atPos, setAtPos] = useState<number | null>(null);

  const validLabels = useMemo(() => new Set(assets.map(a => a.label)), [assets]);

  const filtered = useMemo(() => {
    const lower = filter.toLowerCase();
    if (!lower) return assets;
    return assets.filter(a =>
      a.label.toLowerCase().includes(lower) ||
      a.filename.toLowerCase().includes(lower) ||
      a.kind.toLowerCase().includes(lower)
    );
  }, [filter, assets]);

  useEffect(() => { setActiveIdx(0); }, [filter, popupOpen]);

  // value/cursor 变化 → 维护 filter / 关闭浮层
  useEffect(() => {
    if (atPos === null) return;
    const ta = taRef.current;
    if (!ta) return;
    const cur = ta.selectionStart;
    if (cur < atPos + 1 || atPos >= value.length || value[atPos] !== "@") {
      setPopupOpen(false); setAtPos(null); return;
    }
    const ft = value.slice(atPos + 1, cur);
    if (/\s/.test(ft)) { setPopupOpen(false); setAtPos(null); return; }
    setFilter(ft);
  }, [value, atPos]);

  const onScroll = () => {
    const ta = taRef.current, ov = overlayRef.current;
    if (ta && ov) { ov.scrollTop = ta.scrollTop; ov.scrollLeft = ta.scrollLeft; }
  };

  const onTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    onChange(v);
    const cur = e.target.selectionStart;
    const lastChar = v[cur - 1];
    if (lastChar === "@") {
      // 仅当 @ 前是空白或开头时触发
      const prev = cur >= 2 ? v[cur - 2] : "";
      if (cur === 1 || /\s/.test(prev) || prev === "") {
        setAtPos(cur - 1);
        setPopupOpen(true);
        setFilter("");
        setActiveIdx(0);
      }
    }
  };

  const insertMention = (asset: MentionAsset) => {
    if (atPos === null) return;
    const ta = taRef.current;
    if (!ta) return;
    const before = value.slice(0, atPos);
    const after = value.slice(ta.selectionStart);
    const insert = `@${asset.label} `;
    const next = before + insert + after;
    onChange(next);
    setPopupOpen(false); setAtPos(null);
    requestAnimationFrame(() => {
      const np = before.length + insert.length;
      ta.focus();
      ta.setSelectionRange(np, np);
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!popupOpen || filtered.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      insertMention(filtered[activeIdx]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setPopupOpen(false); setAtPos(null);
    }
  };

  // 高亮渲染
  const overlayContent = useMemo(() => {
    const out: React.ReactNode[] = [];
    let last = 0;
    let m: RegExpExecArray | null;
    RE_MENTION.lastIndex = 0;
    while ((m = RE_MENTION.exec(value)) !== null) {
      if (m.index > last) out.push(value.slice(last, m.index));
      const isValid = validLabels.has(m[1]);
      out.push(
        <span
          key={`m_${m.index}`}
          style={{
            background: isValid ? "#dbeafe" : "#fee2e2",
            color: isValid ? "#1d4ed8" : "#dc2626",
            padding: "1px 5px",
            borderRadius: 4,
            fontWeight: 500,
            border: isValid ? "1px solid #bfdbfe" : "1px dashed #fecaca",
          }}
        >
          {m[0]}
        </span>
      );
      last = m.index + m[0].length;
    }
    if (last < value.length) out.push(value.slice(last));
    out.push("​");
    return out;
  }, [value, validLabels]);

  // 底部已用引用列表(× 删除)
  const usedMentions = useMemo(() => {
    const found = new Map<string, number>();
    let m: RegExpExecArray | null;
    RE_MENTION.lastIndex = 0;
    while ((m = RE_MENTION.exec(value)) !== null) {
      found.set(m[1], (found.get(m[1]) ?? 0) + 1);
    }
    return Array.from(found.entries());
  }, [value]);

  const removeMention = (label: string) => {
    const re = new RegExp(`@${label}\\b\\s?`, "g");
    onChange(value.replace(re, ""));
  };

  const SHARED_TEXT_STYLE: React.CSSProperties = {
    padding: "0.7rem 0.9rem",
    fontSize: "0.9rem",
    lineHeight: 1.5,
    fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif",
    boxSizing: "border-box",
    border: "1px solid #ddd",
    borderRadius: 8,
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      {rightSlot && (
        <div style={{ position: "absolute", top: 8, right: 8, zIndex: 3 }}>{rightSlot}</div>
      )}
      <div
        ref={overlayRef}
        aria-hidden
        style={{
          ...SHARED_TEXT_STYLE,
          position: "absolute",
          inset: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          overflowY: "auto",
          overflowX: "hidden",
          pointerEvents: "none",
          color: "#0d0d0d",
          background: "#fff",
          borderColor: "transparent",
          minHeight: rows * 1.5 + "em",
        }}
      >
        {overlayContent}
      </div>
      <textarea
        ref={taRef}
        value={value}
        onChange={onTextChange}
        onKeyDown={onKeyDown}
        onScroll={onScroll}
        placeholder={placeholder}
        rows={rows}
        disabled={disabled}
        spellCheck={false}
        style={{
          ...SHARED_TEXT_STYLE,
          position: "relative",
          width: "100%",
          background: "transparent",
          color: "transparent",
          caretColor: "#0d0d0d",
          resize: "vertical",
        }}
      />
      {popupOpen && (
        <div style={{
          position: "absolute",
          top: "calc(100% + 4px)", left: 0,
          background: "#fff",
          border: "1px solid #ddd",
          borderRadius: 8,
          boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
          maxHeight: 320, overflowY: "auto",
          minWidth: 320, zIndex: 50,
        }}>
          <div style={{ padding: "0.5rem 0.7rem", fontSize: "0.75rem", color: "#888", borderBottom: "1px solid #f0f0f0" }}>
            选择素材引用 {filter && `· 过滤: "${filter}"`}
          </div>
          {filtered.length === 0 ? (
            <div style={{ padding: "0.8rem", fontSize: "0.85rem", color: "#999" }}>
              未匹配到素材{assets.length === 0 ? "(请先上传视频/图片)" : ""}
            </div>
          ) : (
            filtered.map((a, i) => (
              <div
                key={a.id}
                onMouseDown={(e) => { e.preventDefault(); insertMention(a); }}
                onMouseEnter={() => setActiveIdx(i)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "0.5rem 0.7rem",
                  cursor: "pointer",
                  background: i === activeIdx ? "#f0f7fb" : "transparent",
                }}
              >
                {a.kind === "video" ? (
                  <video src={a.thumb} muted style={{ width: 44, height: 44, borderRadius: 4, objectFit: "cover", background: "#000" }} />
                ) : (
                  <img src={a.thumb} alt="" style={{ width: 44, height: 44, borderRadius: 4, objectFit: "cover" }} />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.85rem", color: "#1d4ed8", fontWeight: 500 }}>@{a.label}</div>
                  <div style={{ fontSize: "0.75rem", color: "#888", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {a.filename}{a.meta ? ` · ${a.meta}` : ""}
                  </div>
                </div>
              </div>
            ))
          )}
          <div style={{ padding: "0.4rem 0.7rem", fontSize: "0.7rem", color: "#aaa", borderTop: "1px solid #f0f0f0" }}>
            ↑↓ 选 · Enter/Tab 插入 · Esc 关闭
          </div>
        </div>
      )}
      {usedMentions.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
          <span style={{ fontSize: "0.72rem", color: "#888", lineHeight: "22px" }}>已用引用:</span>
          {usedMentions.map(([label, n]) => {
            const valid = validLabels.has(label);
            return (
              <span key={label} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                background: valid ? "#dbeafe" : "#fee2e2",
                color: valid ? "#1d4ed8" : "#dc2626",
                fontSize: "0.72rem",
                padding: "1px 6px",
                borderRadius: 10,
                border: valid ? "1px solid #bfdbfe" : "1px dashed #fecaca",
              }}>
                @{label}{n > 1 ? ` ×${n}` : ""}
                <button onClick={() => removeMention(label)}
                  style={{ background: "none", border: "none", color: valid ? "#1d4ed8" : "#dc2626", cursor: "pointer", padding: 0, fontSize: "0.85rem", lineHeight: 1 }}>
                  ×
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
