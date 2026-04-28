"use client";

/**
 * 用户状态本地同步 helper
 *
 * 任何修改本地 user 缓存(localStorage["user"])的地方都应通过这两个函数,
 * 它们内部统一做 setItem + dispatch "user-updated" 事件 — 让 Sidebar 等
 * 组件实时刷新,而不是用户挂载后永远显示旧值。
 *
 * Sidebar 缓存 bug 的根因:充值/扣费成功后只更新页面内 useState,没写
 * localStorage,Sidebar 挂载时读的是登录那一刻的快照。这个 helper 解决
 * "改的人写,看的人能感知"的同步链路。
 *
 * 设计:
 * - SSR / 未登录 / JSON 损坏 全部静默 noop(不抛,不阻塞)
 * - "user-updated" 是同 tab 通信(自定义事件,浏览器规范不会让 setItem
 *   触发本 tab 的 storage 事件,所以必须自定义)
 * - storage 事件是跨 tab 通信(原生),Sidebar 同时监听两个就跨 tab 同步
 *
 * 用法:
 *   import { updateLocalUser, adjustLocalUserCredits } from "@/lib/userState";
 *
 *   updateLocalUser({ credits: 1539 });    // 设绝对值
 *   updateLocalUser({ name: "新名字" });    // 改 name
 *   adjustLocalUserCredits(+30);           // 增量(充值/退款)
 *   adjustLocalUserCredits(-10);           // 增量(扣费,负数)
 */

const EVENT_NAME = "user-updated";

function _readUser(): Record<string, unknown> | null {
  if (typeof window === "undefined") return null;
  const stored = localStorage.getItem("user");
  if (!stored) return null;
  try {
    return JSON.parse(stored) as Record<string, unknown>;
  } catch {
    if (typeof console !== "undefined") {
      console.warn("[userState] localStorage 'user' JSON 解析失败,跳过同步");
    }
    return null;
  }
}

function _writeUser(u: Record<string, unknown>): void {
  localStorage.setItem("user", JSON.stringify(u));
  window.dispatchEvent(new Event(EVENT_NAME));
}

/** 合并 patch 到本地 user(浅合并,只覆盖传入的字段) */
export function updateLocalUser(patch: Record<string, unknown>): void {
  const u = _readUser();
  if (!u) return;
  Object.assign(u, patch);
  _writeUser(u);
}

/** 给 credits 加 delta(正数充值/退款,负数扣费)— 比 updateLocalUser 更适合"我知道增量但不知绝对值"的场景 */
export function adjustLocalUserCredits(delta: number): void {
  const u = _readUser();
  if (!u) return;
  const cur = typeof u.credits === "number" ? u.credits : 0;
  u.credits = cur + delta;
  _writeUser(u);
}

/** 写入 access token + dispatch user-updated → 让订阅了 token 的组件(JobPanel/Sidebar 等)立刻刷新登录态 */
export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem("token", token);
  window.dispatchEvent(new Event(EVENT_NAME));
}

/** 清登录态:删 token + user + refresh_token 并 dispatch — 用于登出/换号/401 强踢 */
export function clearAuthSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("token");
  localStorage.removeItem("user");
  localStorage.removeItem("refresh_token");
  window.dispatchEvent(new Event(EVENT_NAME));
}
