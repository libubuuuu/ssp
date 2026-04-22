"use client";
import { useLang } from "./LanguageContext";

export default function LanguageSwitcher() {
  const { lang, setLang } = useLang();
  return (
    <div style={{ display: "flex", gap: 4, padding: "0.3rem 0.5rem", fontSize: "0.75rem" }}>
      <button
        onClick={() => setLang("zh")}
        style={{
          background: lang === "zh" ? "#0d0d0d" : "transparent",
          color: lang === "zh" ? "#fff" : "#666",
          border: "1px solid #ddd",
          borderRadius: 6,
          padding: "0.2rem 0.5rem",
          cursor: "pointer",
          fontSize: "0.75rem",
        }}
      >
        中文
      </button>
      <button
        onClick={() => setLang("en")}
        style={{
          background: lang === "en" ? "#0d0d0d" : "transparent",
          color: lang === "en" ? "#fff" : "#666",
          border: "1px solid #ddd",
          borderRadius: 6,
          padding: "0.2rem 0.5rem",
          cursor: "pointer",
          fontSize: "0.75rem",
        }}
      >
        EN
      </button>
    </div>
  );
}
