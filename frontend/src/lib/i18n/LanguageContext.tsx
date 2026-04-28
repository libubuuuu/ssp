"use client";
import React, { createContext, useContext, ReactNode } from "react";
import { zh } from "./zh";
import { en } from "./en";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

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
  // 通过 useSyncExternalStore 订阅 localStorage["lang"];SSR/client 首渲一致(默认 "zh"),
  // hydrate 后自动同步真实值,无 set-state-in-effect anti-pattern
  const stored = useLocalStorageItem("lang", "zh");
  const lang: Lang = stored === "en" ? "en" : "zh";

  const setLang = (l: Lang) => {
    // 写 localStorage + dispatch user-updated → useLocalStorageItem 重新读 → re-render
    localStorage.setItem("lang", l);
    window.dispatchEvent(new Event("user-updated"));
  };

  // t("sidebar.dashboard") → "首页" or "Dashboard"
  const t = (key: string): string => {
    const parts = key.split(".");
    let v: unknown = dicts[lang];
    for (const p of parts) {
      if (v && typeof v === "object" && p in (v as Record<string, unknown>)) {
        v = (v as Record<string, unknown>)[p];
      } else return key;
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
