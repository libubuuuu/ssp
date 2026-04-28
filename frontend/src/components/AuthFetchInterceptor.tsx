"use client";
/**
 * 全局 fetch 拦截器(P8 阶段 2:cookie + header 双轨)
 *
 * 触发场景(全部覆盖,前端代码无需改):
 *   - JWT_SECRET 轮换 → 旧 token 签名验证失败
 *   - access token 自然过期(1 小时,主动续期阈值 10 分钟)
 *   - 改密码 / 强制下线 / 用户主动登出所有设备 → 用户级吊销
 *
 * P8 阶段 2 改动:
 *   - 所有 /api/* 请求自动加 credentials: 'include' → httpOnly cookie 自动带
 *   - localStorage 路径保留(过渡期兼容老登录态),后续阶段 3 移除
 *   - refresh 也用 credentials:include,服务端读 cookie refresh_token + 写新 cookie
 *
 * 行为:
 *   1. 任意 /api/* 调用返 401
 *   2. 拦截器自动调 /api/auth/refresh(cookie + body 兜底两路)
 *   3. 成功 → 用新 cookie 自动跑,localStorage 也同步刷新(过渡)
 *   4. 失败 → 静默清 localStorage + /auth?expired=1
 *
 * 关键设计:
 *   - 单例 refreshPromise 防并发(同时多个请求 401 时只刷一次)
 *   - 不拦截 /api/auth/login | register | send-code | login-by-code | reset-password-by-code
 *   - 不拦截 /api/auth/refresh 自己(避免无限循环)
 *   - 已在 /auth 页不再跳(避免循环)
 *   - 模块级单 patch(防 React strict mode 双重渲染重复装)
 */
import { useEffect } from "react";
import { setAuthToken, clearAuthSession } from "@/lib/userState";

const PUBLIC_AUTH_PATHS = [
  "/api/auth/login",
  "/api/auth/register",
  "/api/auth/send-code",
  "/api/auth/login-by-code",
  "/api/auth/reset-password-by-code",
];

// 主动续期阈值:剩余 < 10 分钟时刷新
const PROACTIVE_REFRESH_THRESHOLD_SEC = 600;
// 周期检查间隔:5 分钟
const PROACTIVE_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

/** 解 JWT payload(不验签,只取 exp 看剩余时间) */
function getTokenExp(token: string): number | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // base64url → base64
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
    const payload = JSON.parse(json);
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

export default function AuthFetchInterceptor() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if ((window as unknown as { __authFetchPatched?: boolean }).__authFetchPatched) return;
    (window as unknown as { __authFetchPatched?: boolean }).__authFetchPatched = true;

    const originalFetch = window.fetch.bind(window);
    let refreshPromise: Promise<string | null> | null = null;

    async function tryRefresh(): Promise<string | null> {
      // P8 阶段 2:cookie 优先(credentials:include 自动带 refresh cookie),
      // body 兜底兼容老登录态(localStorage 还在的过渡期)
      const refresh = localStorage.getItem("refresh_token");
      try {
        const r = await originalFetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",  // 关键:让浏览器带 cookie + 接受 set-cookie
          body: JSON.stringify(refresh ? { refresh_token: refresh } : {}),
        });
        if (!r.ok) return null;
        const data = await r.json();
        const newAccess = data.access_token ?? data.token;
        if (newAccess) {
          // 同步写 localStorage(过渡期);新 access cookie 服务端已经 set。dispatch 让 useSyncExternalStore 订阅者刷新
          setAuthToken(newAccess);
          return newAccess;
        }
      } catch {
        // 网络错误等,视为 refresh 失败
      }
      return null;
    }

    function redirectToLogin() {
      try {
        clearAuthSession();
      } catch {}
      const path = window.location.pathname;
      // 已经在登录页 / 注册页就不跳,避免循环
      if (path === "/auth" || path.startsWith("/auth/")) return;
      // 记住用户原本想去哪,登录后跳回
      try {
        sessionStorage.setItem("post_login_redirect", path + window.location.search);
      } catch {}
      window.location.href = "/auth?expired=1";
    }

    function urlOf(input: RequestInfo | URL): string {
      if (typeof input === "string") return input;
      if (input instanceof URL) return input.toString();
      return (input as Request).url ?? "";
    }

    window.fetch = async function patchedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
      const url = urlOf(input);

      // P8 阶段 2:所有 /api/* 自动加 credentials:include(cookie 双向)
      const isApi = url.includes("/api/");
      const isRefresh = url.includes("/api/auth/refresh");
      const isPublicAuth = PUBLIC_AUTH_PATHS.some(p => url.includes(p));

      // 即便公开接口(login/register)也要 credentials:include — 服务端 set 的 cookie 才能进 jar
      let effectiveInit = init;
      if (isApi && (!init || !("credentials" in init))) {
        effectiveInit = { ...(init ?? {}), credentials: "include" };
      }

      if (!isApi || isRefresh || isPublicAuth) {
        return originalFetch(input, effectiveInit);
      }

      const response = await originalFetch(input, effectiveInit);
      if (response.status !== 401) return response;

      // 401 → 试刷 token(并发请求共享同一次刷新)
      if (!refreshPromise) {
        refreshPromise = tryRefresh().finally(() => {
          refreshPromise = null;
        });
      }
      const newToken = await refreshPromise;

      if (!newToken) {
        redirectToLogin();
        return response;
      }

      // 用新 token 重试原请求(cookie 已由 refresh 响应 set;header 也保留兼容)
      const retryInit: RequestInit = effectiveInit ? { ...effectiveInit } : { credentials: "include" };
      const headers = new Headers(retryInit.headers ?? {});
      headers.set("Authorization", `Bearer ${newToken}`);
      retryInit.headers = headers;
      retryInit.credentials = "include";
      const retryResp = await originalFetch(input, retryInit);

      // 重试还 401 = refresh 拿到的 token 也被吊销 / 用户已被踢
      if (retryResp.status === 401) {
        redirectToLogin();
      }
      return retryResp;
    };
  }, []);

  // ========== 主动续期:在 access 快过期前提前刷,用户永远不被中断 ==========
  useEffect(() => {
    if (typeof window === "undefined") return;

    let isProactiveRefreshing = false;

    async function proactiveRefreshIfNeeded() {
      if (isProactiveRefreshing) return;
      const token = localStorage.getItem("token");
      const refresh = localStorage.getItem("refresh_token");
      if (!token || !refresh) return;

      const exp = getTokenExp(token);
      if (exp === null) return;
      const nowSec = Math.floor(Date.now() / 1000);
      const remainSec = exp - nowSec;
      // 已经过期或快过期:刷新
      if (remainSec < PROACTIVE_REFRESH_THRESHOLD_SEC) {
        isProactiveRefreshing = true;
        try {
          // 用原始 fetch 而不是 patched 的(避免 401 拦截重入)
          const originalFetch = window.fetch.bind(window);
          const r = await originalFetch("/api/auth/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",  // P8: 带 refresh cookie
            body: JSON.stringify({ refresh_token: refresh }),
          });
          if (r.ok) {
            const data = await r.json();
            const newAccess = data.access_token ?? data.token;
            if (newAccess) setAuthToken(newAccess);
          }
          // refresh 失败:不主动跳登录,等用户下次实际请求触发 401 再让 fetch 拦截器处理
        } catch {
          // 网络错误:静默,下次再试
        } finally {
          isProactiveRefreshing = false;
        }
      }
    }

    // 启动时检查一次
    proactiveRefreshIfNeeded();

    // 周期检查
    const interval = setInterval(proactiveRefreshIfNeeded, PROACTIVE_REFRESH_INTERVAL_MS);

    // tab 重新可见时立即检查(用户切回浏览器,可能 token 已悄悄过期)
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        proactiveRefreshIfNeeded();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return null;
}
