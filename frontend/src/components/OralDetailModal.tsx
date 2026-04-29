"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface ModelOrProduct {
  name: string;
  image_url: string;
}

interface OralDetail {
  id: string;
  user_id: string;
  user_email: string | null;
  tier: string;
  status: string;
  duration_seconds: number;
  credits_charged: number;
  credits_refunded: number;
  credits_net: number;
  selected_models: ModelOrProduct[] | string | null;
  selected_products: ModelOrProduct[] | string | null;
  asr_transcript: string | null;
  edited_transcript: string | null;
  voice_provider: string | null;
  voice_id: string | null;
  new_audio_url: string | null;
  swap_fal_request_id: string | null;
  swapped_video_url: string | null;
  lipsync_fal_request_id: string | null;
  final_video_url: string | null;
  final_video_archived: string | null;
  mask_image_path: string | null;
  original_video_url: string | null;
  error_step: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

interface Props {
  sessionId: string;
  onClose: () => void;
}

const labelStyle: React.CSSProperties = {
  fontSize: "0.7rem", color: "#888", textTransform: "uppercase",
  letterSpacing: "0.05em", marginBottom: "0.2rem",
};

const sectionStyle: React.CSSProperties = {
  borderTop: "1px solid #eee", paddingTop: "1rem", marginTop: "1rem",
};

const codeStyle: React.CSSProperties = {
  fontFamily: "monospace", fontSize: "0.8rem", background: "#f5f3ed",
  padding: "0.2rem 0.4rem", borderRadius: 4, wordBreak: "break-all",
};

export default function OralDetailModal({ sessionId, onClose }: Props) {
  const { lang } = useLang();
  const isEn = lang === "en";
  const [d, setD] = useState<OralDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = localStorage.getItem("token") ?? "";
        const res = await fetch(`${API_BASE}/api/admin/oral-tasks/${sessionId}`, {
          headers: { Authorization: `Bearer ${token}` },
          credentials: "include",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body: OralDetail = await res.json();
        if (!cancelled) setD(body);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 1000, padding: "1rem",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: "#fff", borderRadius: 12, maxWidth: 900, width: "100%",
        maxHeight: "90vh", display: "flex", flexDirection: "column",
      }}>
        <div style={{ padding: "1rem 1.5rem", borderBottom: "1px solid #eee", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>{isEn ? "Session detail" : "任务详情"}</strong>
          <button onClick={onClose} style={{ background: "none", border: "none", fontSize: "1.2rem", cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ padding: "1rem 1.5rem", overflowY: "auto", flex: 1 }}>
          {error && <div style={{ color: "#c00" }}>{error}</div>}
          {!error && !d && <div style={{ color: "#888" }}>{isEn ? "Loading..." : "加载中..."}</div>}
          {d && (
            <>
              {/* Meta grid */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "0.8rem 1.2rem" }}>
                <Field label="ID"><code style={codeStyle}>{d.id}</code></Field>
                <Field label={isEn ? "User" : "用户"}>{d.user_email ?? d.user_id}</Field>
                <Field label="Tier">{d.tier}</Field>
                <Field label={isEn ? "Status" : "状态"}>{d.status}</Field>
                <Field label={isEn ? "Duration" : "时长"}>{d.duration_seconds?.toFixed(1)}s</Field>
                <Field label={isEn ? "Credits net" : "净扣"}>
                  {d.credits_charged} → {d.credits_net}
                  {d.credits_refunded > 0 && <span style={{ color: "#888" }}>(退{d.credits_refunded})</span>}
                </Field>
                <Field label={isEn ? "Created" : "创建"}>{d.created_at}</Field>
                <Field label={isEn ? "Completed" : "完成"}>{d.completed_at ?? "-"}</Field>
              </div>

              {/* Error 优先放最上面(失败排查最关注的) */}
              {d.error_message && (
                <div style={{ ...sectionStyle, background: "#fff5f5", padding: "0.8rem 1rem", borderRadius: 8, border: "1px solid #ffd6d6", marginTop: "1rem" }}>
                  <div style={{ ...labelStyle, color: "#c00" }}>{isEn ? "Error" : "错误"} ({d.error_step ?? "?"})</div>
                  <div style={{ fontSize: "0.85rem", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{d.error_message}</div>
                </div>
              )}

              {/* Models / Products */}
              <div style={sectionStyle}>
                <div style={labelStyle}>{isEn ? "Models / Products" : "模特 / 产品"}</div>
                <MediaListBlock items={d.selected_models} placeholder="Models" />
                <MediaListBlock items={d.selected_products} placeholder="Products" />
              </div>

              {/* ASR + edited */}
              <div style={sectionStyle}>
                <div style={labelStyle}>① ASR transcript</div>
                <pre style={transcriptStyle}>{d.asr_transcript ?? "-"}</pre>
                <div style={{ ...labelStyle, marginTop: "0.6rem" }}>② {isEn ? "Edited transcript" : "编辑后文案"}</div>
                <pre style={transcriptStyle}>{d.edited_transcript ?? "-"}</pre>
              </div>

              {/* Media artifacts */}
              <div style={sectionStyle}>
                <div style={labelStyle}>{isEn ? "Artifacts" : "中间产物"}</div>
                <MediaRow label={isEn ? "Original video" : "原视频"} url={d.original_video_url} kind="video" />
                <MediaRow label={isEn ? "Mask" : "Mask 图"} url={d.mask_image_path ? d.mask_image_path.replace("/opt/ssp/uploads", "/uploads") : null} kind="image" />
                <MediaRow label={isEn ? "③ New audio" : "③ 新音频"} url={d.new_audio_url} kind="audio" />
                <MediaRow label={isEn ? "④ Swapped video" : "④ 换装视频"} url={d.swapped_video_url} kind="video" />
                <MediaRow label={isEn ? "⑤ Final" : "⑤ 最终视频"} url={d.final_video_url} kind="video" />
              </div>

              {/* FAL request IDs */}
              <div style={sectionStyle}>
                <div style={labelStyle}>FAL request IDs</div>
                <Field label={isEn ? "Voice provider" : "音色"}>
                  {d.voice_provider ? `${d.voice_provider} / ${d.voice_id ?? "-"}` : "-"}
                </Field>
                <Field label={isEn ? "Swap (Step 4)" : "换装 (Step 4)"}>
                  <code style={codeStyle}>{d.swap_fal_request_id ?? "-"}</code>
                </Field>
                <Field label={isEn ? "Lipsync (Step 5)" : "口型 (Step 5)"}>
                  <code style={codeStyle}>{d.lipsync_fal_request_id ?? "-"}</code>
                </Field>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const transcriptStyle: React.CSSProperties = {
  background: "#f5f3ed", padding: "0.8rem", borderRadius: 6,
  fontSize: "0.85rem", whiteSpace: "pre-wrap", wordBreak: "break-word",
  fontFamily: "inherit", margin: 0,
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={labelStyle}>{label}</div>
      <div style={{ fontSize: "0.85rem" }}>{children}</div>
    </div>
  );
}

function MediaRow({ label, url, kind }: { label: string; url: string | null; kind: "video" | "image" | "audio" }) {
  if (!url) {
    return (
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.4rem 0", fontSize: "0.85rem", color: "#999" }}>
        <span>{label}</span><span>-</span>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.4rem 0", fontSize: "0.85rem", gap: "0.5rem" }}>
      <span style={{ flex: 0, minWidth: 100 }}>{label}</span>
      {kind === "image" && <img src={url} alt={label} style={{ height: 60, borderRadius: 4 }} />}
      {kind === "video" && <video src={url} controls style={{ height: 60, borderRadius: 4 }} />}
      {kind === "audio" && <audio src={url} controls style={{ height: 32 }} />}
      <a href={url} target="_blank" rel="noreferrer" style={{ fontSize: "0.7rem", color: "#0d6efd", flex: 0 }}>↗</a>
    </div>
  );
}

function MediaListBlock({ items, placeholder }: { items: ModelOrProduct[] | string | null; placeholder: string }) {
  if (!items) return null;
  if (typeof items === "string") {
    return <pre style={{ ...transcriptStyle, color: "#c00" }}>{`${placeholder} (raw): ${items}`}</pre>;
  }
  if (items.length === 0) return null;
  return (
    <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", marginTop: "0.4rem" }}>
      {items.map((it, i) => (
        <div key={i} style={{ border: "1px solid #eee", borderRadius: 6, padding: 4, fontSize: "0.75rem", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <img src={it.image_url} alt={it.name} style={{ height: 40, width: 40, objectFit: "cover", borderRadius: 4 }} />
          <span>{it.name}</span>
        </div>
      ))}
    </div>
  );
}
