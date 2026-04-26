"use client";
/**
 * 全局 fetch 拦截器,统一处理 401 token 失效场景。
 *
 * 触发场景(全部覆盖,前端代码无需改):
 *   - JWT_SECRET 轮换 → 旧 token 签名验证失败
 *   - access token 自然过期(7 天 / 未来 1 小时)
 *   - 改密码 / 强制下线 / 用户主动登出所有设备 → 用户级吊销
 *
 * 行为:
 *   1. 任意 /api/* 调用返 401
 *   2. 拦截器自动调 /api/auth/refresh 用 refresh_token 换新 access
 *   3. 成功 → 用新 access 重试原请求,业务代码无感知
 *   4. 失败 → 静默清 localStorage,跳 /auth?expired=1
 *
 * 关键设计:
 *   - 单例 refreshPromise 防并发(同时多个请求 401 时只刷一次)
 *   - 不拦截 /api/auth/login | register | send-code | login-by-code | reset-password-by-code
 *     (公开接口的 401 是真错误,该让前端代码自己处理)
 *   - 不拦截 /api/auth/refresh 自己(避免无限循环)
 *   - 已在 /auth 页不再跳(避免循环)
 *   - 模块级单 patch(防 React strict mode 双重渲染重复装)
 */
import { useEffect } from "react";

const PUBLIC_AUTH_PATHS = [
  "/api/auth/login",
  "/api/auth/register",
  "/api/auth/send-code",
  "/api/auth/login-by-code",
  "/api/auth/reset-password-by-code",
];

export default function AuthFetchInterceptor() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if ((window as unknown as { __authFetchPatched?: boolean }).__authFetchPatched) return;
    (window as unknown as { __authFetchPatched?: boolean }).__authFetchPatched = true;

    const originalFetch = window.fetch.bind(window);
    let refreshPromise: Promise<string | null> | null = null;

    async function tryRefresh(): Promise<string | null> {
      const refresh = localStorage.getItem("refresh_token");
      if (!refresh) return null;
      try {
        const r = await originalFetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!r.ok) return null;
        const data = await r.json();
        const newAccess = data.access_token ?? data.token;
        if (newAccess) {
          localStorage.setItem("token", newAccess);
          return newAccess;
        }
      } catch {
        // 网络错误等,视为 refresh 失败
      }
      return null;
    }

    function redirectToLogin() {
      try {
        localStorage.removeItem("token");
        localStorage.removeItem("refresh_token");
        localStorage.removeItem("user");
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

      // 不拦截:不是 API 调用 / 是 refresh 自己 / 是公开 auth 接口
      const isApi = url.includes("/api/");
      const isRefresh = url.includes("/api/auth/refresh");
      const isPublicAuth = PUBLIC_AUTH_PATHS.some(p => url.includes(p));

      if (!isApi || isRefresh || isPublicAuth) {
        return originalFetch(input, init);
      }

      let response = await originalFetch(input, init);
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

      // 用新 token 重试原请求
      const retryInit: RequestInit = init ? { ...init } : {};
      const headers = new Headers(retryInit.headers ?? {});
      headers.set("Authorization", `Bearer ${newToken}`);
      retryInit.headers = headers;
      const retryResp = await originalFetch(input, retryInit);

      // 重试还 401 = refresh 拿到的 token 也被吊销 / 用户已被踢
      if (retryResp.status === 401) {
        redirectToLogin();
      }
      return retryResp;
    };
  }, []);

  return null;
}
