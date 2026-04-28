"use client";

import { useSyncExternalStore } from "react";

const MOBILE_BREAKPOINT = 768;

/**
 * 订阅 viewport 是否窄屏 (< 768px)。SSR 安全。
 *
 * 为什么不用 useState + useEffect resize listener?
 *   同 useLocalStorageItem 的理由:effect body setState 是 anti-pattern。
 *   useSyncExternalStore 的 server snapshot 默认 false,匹配 desktop-first
 *   渲染策略,client hydrate 后立刻同步真实值。
 */
export function useIsMobile(): boolean {
  const subscribe = (callback: () => void) => {
    if (typeof window === "undefined") return () => {};
    window.addEventListener("resize", callback);
    return () => window.removeEventListener("resize", callback);
  };

  const getSnapshot = () => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < MOBILE_BREAKPOINT;
  };

  // SSR 默认按桌面端渲染(避免初始一闪移动端样式)
  const getServerSnapshot = () => false;

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
