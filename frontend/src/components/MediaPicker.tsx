"use client";
import { useEffect, useState } from "react";
import { useLang } from "@/lib/i18n/LanguageContext";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export interface MediaPickerItem {
  name: string;
  image_url: string;
}

interface Props {
  source: "products" | "history";
  open: boolean;
  onClose: () => void;
  onPick: (item: MediaPickerItem) => void;
}

const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

export default function MediaPicker({ source, open, onClose, onPick }: Props) {
  const { t } = useLang();
  const [items, setItems] = useState<MediaPickerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError("");
      try {
        if (source === "products") {
          const res = await fetch(`${API_BASE}/api/products?limit=24&is_published=true`, {
            credentials: "include",
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data: Array<{ name: string; thumbnail_url: string | null; images: string[] | null }> = await res.json();
          const out: MediaPickerItem[] = [];
          for (const p of data) {
            const url = p.thumbnail_url || (p.images && p.images[0]) || "";
            if (url) out.push({ name: p.name, image_url: url });
          }
          if (!cancelled) setItems(out);
        } else {
          const res = await fetch(`${API_BASE}/api/tasks/history`, {
            headers: { Authorization: `Bearer ${token()}` },
            credentials: "include",
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data: { history: Array<{ id: string; module: string; prompt: string; images: string[]; created_at: string }> } = await res.json();
          const out: MediaPickerItem[] = [];
          for (const h of data.history) {
            if (!h.images || h.images.length === 0) continue;
            // 多图任务每张展开成独立 item
            for (let i = 0; i < h.images.length; i++) {
              const label = (h.prompt || h.module || h.id).slice(0, 24);
              const name = h.images.length > 1 ? `${label} #${i + 1}` : label;
              out.push({ name, image_url: h.images[i] });
            }
            if (out.length >= 60) break;
          }
          if (!cancelled) setItems(out);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message || t("oral.picker.loadFail"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, source, t]);

  if (!open) return null;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000, padding: "1rem",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "#fff", borderRadius: 12, maxWidth: 800, width: "100%",
          maxHeight: "80vh", display: "flex", flexDirection: "column",
        }}
      >
        <div style={{ padding: "1rem 1.5rem", borderBottom: "1px solid #eee", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>{source === "products" ? t("oral.picker.titleProducts") : t("oral.picker.titleHistory")}</strong>
          <button onClick={onClose} style={{ background: "none", border: "none", fontSize: "1.2rem", cursor: "pointer" }}>✕</button>
        </div>
        <div style={{ padding: "1rem 1.5rem", overflowY: "auto", flex: 1 }}>
          {loading && <div style={{ color: "#888" }}>{t("oral.picker.loading")}</div>}
          {error && <div style={{ color: "#c00" }}>{error}</div>}
          {!loading && !error && items.length === 0 && (
            <div style={{ color: "#888" }}>{t("oral.picker.empty")}</div>
          )}
          {!loading && items.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: "0.8rem" }}>
              {items.map((it, idx) => (
                <button
                  key={`${it.image_url}_${idx}`}
                  onClick={() => { onPick(it); onClose(); }}
                  style={{
                    background: "none", border: "1px solid #eee", borderRadius: 8,
                    padding: 0, cursor: "pointer", overflow: "hidden",
                    transition: "border-color 0.15s",
                  }}
                  onMouseEnter={e => (e.currentTarget.style.borderColor = "#0d0d0d")}
                  onMouseLeave={e => (e.currentTarget.style.borderColor = "#eee")}
                >
                  <img
                    src={it.image_url}
                    alt={it.name}
                    style={{ width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" }}
                    loading="lazy"
                  />
                  <div style={{ padding: "0.4rem 0.5rem", fontSize: "0.75rem", color: "#333", textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {it.name}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
