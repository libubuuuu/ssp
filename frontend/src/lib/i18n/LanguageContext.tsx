"use client";
import React, { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { zh } from "./zh";
import { en } from "./en";

type Lang = "zh" | "en";
const dicts = { zh, en };

interface LangContextType {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
}

const LangContext = createContext<LangContextType>({
  lang: "zh",
  setLang: () => {},
  t: (k) => k,
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("zh");

  // 启动时读 localStorage
  useEffect(() => {
    const saved = localStorage.getItem("lang") as Lang;
    if (saved === "zh" || saved === "en") setLangState(saved);
  }, []);

  const setLang = (l: Lang) => {
    setLangState(l);
    localStorage.setItem("lang", l);
  };

  // t("sidebar.dashboard") → "首页" or "Dashboard"
  const t = (key: string): string => {
    const parts = key.split(".");
    let v: any = dicts[lang];
    for (const p of parts) {
      if (v && typeof v === "object" && p in v) v = v[p];
      else return key;
    }
    return typeof v === "string" ? v : key;
  };

  return (
    <LangContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LangContext.Provider>
  );
}

export function useLang() {
  return useContext(LangContext);
}
