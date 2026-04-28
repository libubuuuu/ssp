"use client";

import { useSyncExternalStore } from "react";

/**
 * 订阅 localStorage 单 key 的当前值,SSR 安全。
 *
 * 为什么 useSyncExternalStore 而不是 useState + useEffect?
 *   useEffect body 里 setState(localStorage.getItem(...)) 是 React 19
 *   严格模式下的 anti-pattern (react-hooks/set-state-in-effect lint
 *   错):造成挂载时一次额外渲染 + 渲染期级联。useSyncExternalStore 是
 *   官方推荐的"订阅外部 store"机制,SSR snapshot 与 client snapshot 分
 *   离,无 hydration mismatch。
 *
 * 跨 tab + 跨同 tab 同步:
 *   - "storage" 事件:浏览器原生,跨 tab(同源不同窗口)
 *   - "user-updated" 事件:userState helper 写 localStorage 后 dispatch,
 *     同 tab 通信(浏览器规范不让 setItem 触发本 tab 的 storage 事件)
 *
 * 用法:
 *   const lang = useLocalStorageItem("lang", "zh");      // 总能拿到 string
 *   const token = useLocalStorageItem("token", null);    // 没登录时是 null
 */
export function useLocalStorageItem(key: string): string | null;
export function useLocalStorageItem(key: string, defaultValue: string): string;
export function useLocalStorageItem(key: string, defaultValue: string | null = null): string | null {
  const subscribe = (callback: () => void) => {
    if (typeof window === "undefined") return () => {};
    window.addEventListener("storage", callback);
    window.addEventListener("user-updated", callback);
    return () => {
      window.removeEventListener("storage", callback);
      window.removeEventListener("user-updated", callback);
    };
  };

  const getSnapshot = () => {
    if (typeof window === "undefined") return defaultValue;
    return localStorage.getItem(key) ?? defaultValue;
  };

  // SSR snapshot:server 没有 localStorage,直接返默认值。client 首次渲染也用这个,
  // 保证 hydration 一致;hydrate 后立刻 subscribe 触发重渲染同步真实值
  const getServerSnapshot = () => defaultValue;

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
