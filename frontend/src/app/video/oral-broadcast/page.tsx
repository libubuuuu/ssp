"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface OralSession {
  session_id: string;
  tier: string;
  status: string;
  duration_seconds: number;
  final_video_url?: string | null;
  title: string;
  created_at: string;
}

export default function OralBroadcastListPage() {
  const { t } = useLang();
  const router = useRouter();
  const [sessions, setSessions] = useState<OralSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  const token = () => (typeof window !== "undefined" ? localStorage.getItem("token") || "" : "");

  const loadSessions = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/oral/list`, {
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      if (!res.ok) return;
      const data = await res.json();
      setSessions(data.sessions || []);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => {
    loadSessions();
    const i = setInterval(loadSessions, 5000);
    return () => clearInterval(i);
  }, [loadSessions]);

  const createNew = async (file: File) => {
    setError("");
    if (!file.type.startsWith("video/")) {
      setError(t("oral.errVideoOnly"));
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/oral/upload`, {
        method: "POST",
        body: fd,
        headers: { Authorization: `Bearer ${token()}` },
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || t("oral.errUploadFail"));
        return;
      }
      router.push(`/video/oral-broadcast/${data.session_id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("oral.errUploadFail"));
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#fbfaf6" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 3rem", maxWidth: 1100 }}>
        <div style={{ marginBottom: "2rem" }}>
          <h1 style={{ fontSize: "2rem", fontWeight: 600, margin: 0 }}>
            🎤 {t("oral.title")}
          </h1>
          <p style={{ color: "#888", fontSize: "0.9rem", marginTop: "0.5rem" }}>
            {t("oral.subtitle")}
          </p>
        </div>

        {error && (
          <div style={{ padding: "0.8rem 1rem", background: "#fee", color: "#c33", borderRadius: 8, marginBottom: "1rem" }}>
            {error}
          </div>
        )}

        <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "2rem" }}>
          <label style={{
            display: "inline-block", padding: "0.8rem 1.5rem",
            background: uploading ? "#ddd" : "#0d0d0d", color: "#fff",
            borderRadius: 10, cursor: uploading ? "not-allowed" : "pointer", fontWeight: 500,
          }}>
            {uploading ? t("oral.uploading") : `+ ${t("oral.newSession")}`}
            <input type="file" accept="video/*" disabled={uploading}
              onChange={e => { const f = e.target.files?.[0]; if (f) createNew(f); }}
              style={{ display: "none" }} />
          </label>
          <span style={{ color: "#999", fontSize: "0.85rem" }}>{t("oral.maxDuration")}</span>
        </div>

        {loading ? (
          <div style={{ color: "#888" }}>{t("oral.loading")}</div>
        ) : sessions.length === 0 ? (
          <div style={{ padding: "3rem", background: "#fff", borderRadius: 12, textAlign: "center", color: "#999" }}>
            {t("oral.empty")}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "1rem" }}>
            {sessions.map(s => (
              <div key={s.session_id}
                onClick={() => router.push(`/video/oral-broadcast/${s.session_id}`)}
                style={{ padding: "1rem", background: "#fff", borderRadius: 12, cursor: "pointer", border: "1px solid #eee" }}>
                <div style={{ fontWeight: 500, marginBottom: "0.4rem" }}>{s.title}</div>
                <div style={{ fontSize: "0.8rem", color: "#888" }}>
                  {t(`oral.status.${s.status}`) || s.status}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#aaa", marginTop: "0.4rem" }}>
                  {new Date(s.created_at).toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
