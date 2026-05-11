"use client";
import { useLang } from "./LanguageContext";

export default function LanguageSwitcher() {
  const { lang, setLang } = useLang();
  const next = lang === "zh" ? "en" : "zh";
  const label = lang === "zh" ? "中" : "EN";
  return (
    <button
      onClick={() => setLang(next)}
      title={lang === "zh" ? "切换到英文" : "Switch to Chinese"}
      style={{
        width: "32px",
        height: "32px",
        borderRadius: "50%",
        background: "#f5f3ed",
        color: "#0d0d0d",
        border: "1px solid #ddd",
        cursor: "pointer",
        fontSize: "0.75rem",
        fontWeight: 600,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        flexShrink: 0,
      }}
    >
      {label}
    </button>
  );
}
