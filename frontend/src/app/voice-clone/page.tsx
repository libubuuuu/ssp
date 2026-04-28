"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

const MODES = [
  { key: "clone", labelKey: "modeClone", descKey: "modeCloneDesc" },
  { key: "tts", labelKey: "modeTts", descKey: "modeTtsDesc" },
];

interface VoicePreset { id: string; name: string; gender: string; style: string; }

export default function VoiceClonePage() {
  const { t } = useLang();
  const [mode, setMode] = useState("clone");
  const [referenceAudio, setReferenceAudio] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [selectedVoice, setSelectedVoice] = useState("default");
  const [voicePresets, setVoicePresets] = useState<VoicePreset[]>([]);
  const [resultAudioUrl, setResultAudioUrl] = useState<string | null>(null);
  const [clonedVoiceId, setClonedVoiceId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [gallery, setGallery] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API_BASE}/api/avatar/voice/presets`).then(r => r.json()).then(d => { if (d.voices) setVoicePresets(d.voices); }).catch(() => {});
    const saved = localStorage.getItem("voice_gallery");
    if (saved) { try { setGallery(JSON.parse(saved)); } catch {} }
  }, []);

  const saveGallery = (g: any[]) => {
    setGallery(g);
    localStorage.setItem("voice_gallery", JSON.stringify(g.slice(0, 50)));
  };

  const handleAudioUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => setReferenceAudio(e.target?.result as string);
    reader.readAsDataURL(file);
  };

  const handleClone = async () => {
    if (!referenceAudio || !text) { setError("请上传参考音频并输入文案"); return; }
    setError(""); setLoading(true); setResultAudioUrl(null);
    try {
      const res = await fetch(`${API_BASE}/api/avatar/voice/clone`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference_audio_url: referenceAudio, text, model: "qwen3-tts" }),
      });
      const data = await res.json();
      if (data.voice_id) {
        if (typeof data.cost === "number" && data.cost > 0) adjustLocalUserCredits(-data.cost);
        setClonedVoiceId(data.voice_id);
        setResultAudioUrl(data.audio_url);
        saveGallery([{ url: data.audio_url, label: text.slice(0, 30), mode: "克隆", time: Date.now() }, ...gallery]);
      } else { setError(data.detail || "克隆失败"); }
    } catch (e: any) { setError(e.message || t("errors.networkError")); }
    finally { setLoading(false); }
  };

  const handleTTS = async () => {
    if (!text) { setError("请输入文案"); return; }
    setError(""); setLoading(true); setResultAudioUrl(null);
    try {
      const res = await fetch(`${API_BASE}/api/avatar/voice/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice_id: selectedVoice, speed: 1.0, pitch: 1.0 }),
      });
      const data = await res.json();
      if (data.audio_url) {
        if (typeof data.cost === "number" && data.cost > 0) adjustLocalUserCredits(-data.cost);
        setResultAudioUrl(data.audio_url);
        saveGallery([{ url: data.audio_url, label: text.slice(0, 30), mode: "TTS", time: Date.now() }, ...gallery]);
      } else { setError(data.detail || t("errors.generationFailed")); }
    } catch (e: any) { setError(e.message || t("errors.networkError")); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "0.3rem" }}>{t("voice.title")}</div>
            <h1 style={{ fontSize: "1.6rem", fontWeight: 400, color: "#0d0d0d", margin: 0, fontFamily: "Georgia,serif" }}>{t("voice.titleMain")}<span style={{ fontStyle: "italic" }}> {t("voice.titleAccent")}</span></h1>
          </div>
          {gallery.length > 0 && <button onClick={() => { if (confirm("清空记录？")) saveGallery([]); }} style={{ background: "none", border: "1px solid #ddd", padding: "0.5rem 1rem", borderRadius: "999px", color: "#666", fontSize: "0.85rem", cursor: "pointer" }}>清空记录</button>}
        </div>

        <div style={{ background: "#fafaf7", backgroundImage: "linear-gradient(rgba(0,0,0,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,0.05) 1px,transparent 1px)", backgroundSize: "40px 40px", borderRadius: "24px", minHeight: "calc(100vh - 180px)", padding: "2rem", border: "2px dashed rgba(0,0,0,0.2)" }}>
          {gallery.length === 0 && !loading && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "500px" }}>
              <div style={{ fontSize: "3.5rem", marginBottom: "1rem", color: "#ddd" }}>🎙️</div>
              <div style={{ fontSize: "0.95rem", color: "#999" }}>{t("voice.emptyWorks")}</div>
              <div style={{ fontSize: "0.8rem", color: "#bbb", marginTop: "0.5rem" }}>{t("voice.emptyTip")}</div>
            </div>
          )}
          {loading && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "500px" }}>
              <div style={{ width: "40px", height: "40px", border: "3px solid #eee", borderTopColor: "#0d0d0d", borderRadius: "50%", animation: "spin 1s linear infinite" }}></div>
              <div style={{ marginTop: "1rem", color: "#555", fontSize: "0.95rem", fontWeight: 500 }}>{t("voice.generating")}</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length > 0 && !loading && (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {gallery.map((item, i) => (
                <div key={i} style={{ borderRadius: "14px", overflow: "hidden", background: "#fff", padding: "1rem 1.25rem", boxShadow: "0 4px 12px rgba(0,0,0,0.04)" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.75rem" }}>
                    <span style={{ fontSize: "0.8rem", color: "#999" }}>{item.mode} · {item.label}</span>
                    <a href={item.url} download target="_blank" style={{ fontSize: "0.75rem", color: "#666", textDecoration: "none", border: "1px solid #ddd", padding: "0.25rem 0.6rem", borderRadius: "999px" }}>{t("common.download")}</a>
                  </div>
                  <audio src={item.url} controls style={{ width: "100%" }} />
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      <aside style={{ width: "340px", background: "#fff", borderLeft: "1px solid rgba(0,0,0,0.06)", padding: "2rem 1.75rem", display: "flex", flexDirection: "column", gap: "1.25rem", height: "100vh", position: "sticky", top: 0, overflowY: "auto" }}>
        <div>
          <div style={{ fontSize: "0.72rem", color: "#999", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "0.6rem" }}>{t("voice.secMode")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
            {MODES.map(m => (
              <button key={m.key} onClick={() => setMode(m.key)} style={{ textAlign: "left", padding: "0.7rem 0.9rem", border: mode === m.key ? "2px solid #0d0d0d" : "1px solid #e5e5e5", background: mode === m.key ? "#f9f7f2" : "#fff", borderRadius: "10px", cursor: "pointer" }}>
                <div style={{ fontSize: "0.88rem", fontWeight: 500, color: "#0d0d0d" }}>{t(`voice.${m.labelKey}`)}</div>
                <div style={{ fontSize: "0.72rem", color: "#888", marginTop: "0.15rem" }}>{t(`voice.${m.descKey}`)}</div>
              </button>
            ))}
          </div>
        </div>

        {mode === "clone" && (
          <div>
            <div style={{ fontSize: "0.72rem", color: "#999", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "0.6rem" }}>{t("voice.secReference")}</div>
            <label style={{ display: "block", width: "100%", padding: "1.2rem 0.9rem", border: "2px dashed #ccc", background: "#fafaf7", borderRadius: "12px", cursor: "pointer", color: "#888", fontSize: "0.85rem", textAlign: "center" }}>
              <input type="file" accept="audio/*" onChange={handleAudioUpload} style={{ display: "none" }} />
              {referenceAudio ? t("voice.uploadedRef") : t("voice.clickUploadRef")}
            </label>
            {referenceAudio && <audio src={referenceAudio} controls style={{ width: "100%", marginTop: "0.5rem" }} />}
          </div>
        )}

        {mode === "tts" && voicePresets.length > 0 && (
          <div>
            <div style={{ fontSize: "0.72rem", color: "#999", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "0.6rem" }}>{t("voice.secVoice")}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
              {voicePresets.map(v => (
                <button key={v.id} onClick={() => setSelectedVoice(v.id)} style={{ textAlign: "left", padding: "0.6rem 0.9rem", border: selectedVoice === v.id ? "2px solid #0d0d0d" : "1px solid #e5e5e5", background: selectedVoice === v.id ? "#f9f7f2" : "#fff", borderRadius: "10px", cursor: "pointer" }}>
                  <div style={{ fontSize: "0.85rem", fontWeight: 500, color: "#0d0d0d" }}>{t(`voice.voice_${v.id}`) === `voice.voice_${v.id}` ? v.name : t(`voice.voice_${v.id}`)}</div>
                  <div style={{ fontSize: "0.72rem", color: "#888" }}>{v.gender === "female" ? t("voice.genderFemale") : t("voice.genderMale")} · {t(`voice.voice_${v.id}_style`) === `voice.voice_${v.id}_style` ? v.style : t(`voice.voice_${v.id}_style`)}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: "0.72rem", color: "#999", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: "0.6rem" }}>{t("voice.secText")}</div>
          <textarea value={text} onChange={e => setText(e.target.value)} placeholder={t("voice.textPlaceholder")} style={{ width: "100%", padding: "0.75rem 0.9rem", border: "1px solid #e5e5e5", borderRadius: "12px", fontSize: "0.88rem", minHeight: "120px", resize: "vertical", fontFamily: "inherit", background: "#fff", color: "#333", flex: 1 }} />
        </div>

        {error && <div style={{ color: "#c00", background: "#ffeaea", padding: "0.7rem", borderRadius: "10px", fontSize: "0.8rem" }}>{error}</div>}

        <button onClick={mode === "clone" ? handleClone : handleTTS} disabled={loading} style={{ padding: "0.9rem", background: loading ? "#999" : "#0d0d0d", color: "#fff", border: "none", borderRadius: "12px", cursor: loading ? "wait" : "pointer", fontSize: "0.95rem", fontWeight: 500 }}>
          {loading ? t("voice.generatingBtn") : mode === "clone" ? t("voice.cloneAndGen") : t("voice.genTts")}
        </button>
      </aside>
    </div>
  );
}
