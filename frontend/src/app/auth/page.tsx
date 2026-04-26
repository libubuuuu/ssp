"use client";
import { useLang } from "@/lib/i18n/LanguageContext";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Mode = "login" | "register" | "email_code";

export default function AuthPage() {
  const { lang, t } = useLang();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [totpCode, setTotpCode] = useState("");
  const [need2FA, setNeed2FA] = useState(false);
  const [emailCode, setEmailCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  // 看 ?expired=1 显示"会话已过期"提示(由 AuthFetchInterceptor 401 重定向触发)
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("expired") === "1") {
      setSessionExpired(true);
      // 清掉 query,刷新后不再显示
      const cleanUrl = window.location.pathname;
      window.history.replaceState({}, "", cleanUrl);
    }
  }, []);

  const goAfterLogin = (data: { token: string; access_token?: string; refresh_token?: string; user: unknown }) => {
    // 后端同时返 access_token + refresh_token + 兼容字段 token;优先用 access_token
    localStorage.setItem("token", data.access_token ?? data.token);
    if (data.refresh_token) {
      localStorage.setItem("refresh_token", data.refresh_token);
    }
    localStorage.setItem("user", JSON.stringify(data.user));
    // 登录后清掉过期回跳标记 + 优先去之前被中断的页面
    const redirect = typeof window !== "undefined" ? sessionStorage.getItem("post_login_redirect") : null;
    if (redirect) {
      sessionStorage.removeItem("post_login_redirect");
      router.push(redirect);
      return;
    }
    if (typeof window !== "undefined" && window.location.hostname.startsWith("admin.")) {
      router.push("/admin/orders");
    } else {
      router.push("/");
    }
  };

  const handleSubmit = async () => {
    setError(""); setLoading(true);
    try {
      if (mode === "email_code") {
        const res = await fetch(`${API_BASE}/api/auth/login-by-code`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: form.email, code: emailCode }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "登录失败");
        goAfterLogin(data);
        return;
      }
      const url = mode === "login" ? `${API_BASE}/api/auth/login` : `${API_BASE}/api/auth/register`;
      const body = mode === "login" ? { ...form, totp_code: totpCode } : form;
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        if (typeof data.detail === "object" && data.detail?.need_2fa) {
          setNeed2FA(true);
          setError("");
          return;
        }
        throw new Error(typeof data.detail === "string" ? data.detail : "请求失败");
      }
      setNeed2FA(false);
      setTotpCode("");
      goAfterLogin(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.networkError"));
    } finally {
      setLoading(false);
    }
  };

  const handleSendCode = async () => {
    if (!form.email) { setError(t("auth.inputEmailFirst")); return; }
    setError(""); setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/send-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: form.email, purpose: "login" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : t("errors.networkError"));
      setCodeSent(true);
      setCountdown(60);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errors.networkError"));
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (m: Mode) => {
    setMode(m);
    setError("");
    setNeed2FA(false);
    setTotpCode("");
    setEmailCode("");
    setCodeSent(false);
  };

  const tabBtn = (m: Mode, label: string) => (
    <button
      key={m}
      type="button"
      onClick={() => switchMode(m)}
      style={{
        flex: 1,
        padding: "0.6rem",
        background: mode === m ? "#2a2a2a" : "transparent",
        border: "none",
        borderBottom: mode === m ? "2px solid #f59e0b" : "2px solid transparent",
        color: mode === m ? "#f59e0b" : "#888",
        cursor: "pointer",
        fontSize: "0.9rem",
        fontWeight: mode === m ? 600 : 400,
      }}
    >
      {label}
    </button>
  );

  return (
    <div style={{minHeight:"100vh",background:"#0a0a0a",display:"flex",alignItems:"center",justifyContent:"center"}}>
      <div style={{background:"#1a1a1a",padding:"2rem",borderRadius:"12px",width:"100%",maxWidth:"400px"}}>
        <h2 style={{color:"#fff",textAlign:"center",marginBottom:"0.5rem"}}>
          {mode === "register" ? (lang==="en"?"Sign Up":"注册") : (lang==="en"?"Log In":"登录")}
        </h2>
        <p style={{color:"#888",textAlign:"center",marginBottom:"1.5rem",fontSize:"0.9rem"}}>
          {mode === "register"
            ? (lang==="en"?"Create account, get 100 free credits":"创建新账号，赠送 100 积分")
            : mode === "email_code"
              ? t("auth.codeLoginTip")
              : (lang==="en"?"Welcome back":"欢迎回来")}
        </p>

        {sessionExpired && (
          <div style={{padding:"0.75rem 1rem",marginBottom:"1rem",background:"#3a2a1a",border:"1px solid #8a5a2a",borderRadius:"8px",color:"#fbbf24",fontSize:"0.85rem",textAlign:"center"}}>
            {lang==="en" ? "Session expired, please log in again" : "会话已过期,请重新登录"}
          </div>
        )}

        <div style={{display:"flex",borderBottom:"1px solid #2a2a2a",marginBottom:"1.5rem"}}>
          {tabBtn("login", t("auth.passwordLogin"))}
          {tabBtn("email_code", t("auth.codeLogin"))}
          {tabBtn("register", t("auth.registerTitle"))}
        </div>

        {mode === "register" && (
          <input placeholder={lang==="en"?"Nickname":"昵称"} value={form.name}
            onChange={e => setForm({...form, name: e.target.value})}
            style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        )}
        <input type="email" placeholder={lang==="en"?"Email":"邮箱"} value={form.email}
          onChange={e => setForm({...form, email: e.target.value})}
          style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>

        {(mode === "login" || mode === "register") && (
          <input type="password" placeholder={lang==="en"?"Password":"密码"} value={form.password}
            onChange={e => setForm({...form, password: e.target.value})}
            style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        )}

        {mode === "email_code" && (
          <>
            <button type="button" onClick={handleSendCode} disabled={loading || countdown > 0}
              style={{width:"100%",padding:"0.6rem",marginBottom:"0.75rem",background:countdown>0?"#222":"transparent",border:"1px solid #f59e0b",borderRadius:"8px",color:countdown>0?"#666":"#f59e0b",cursor:countdown>0?"default":"pointer",fontSize:"0.9rem"}}>
              {countdown > 0
                ? `${countdown}s · ${t("auth.resend")}`
                : codeSent ? t("auth.resend") : t("auth.sendCode")}
            </button>
            {codeSent && (
              <div style={{color:"#0a7",fontSize:"0.8rem",marginBottom:"0.75rem"}}>
                {t("auth.codeSentTip")}
              </div>
            )}
            <input value={emailCode}
              onChange={e => setEmailCode(e.target.value.replace(/\D/g,"").slice(0,6))}
              placeholder={t("auth.codePlaceholder")} maxLength={6} inputMode="numeric"
              style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"1.1rem",textAlign:"center",letterSpacing:"0.3rem",fontFamily:"monospace",boxSizing:"border-box"}}/>
          </>
        )}

        {need2FA && mode === "login" && (
          <div style={{marginBottom:"1rem",padding:"0.75rem",background:"#1a2a1a",border:"1px solid #0a7",borderRadius:"8px"}}>
            <div style={{color:"#0a7",fontSize:"0.85rem",marginBottom:"0.5rem"}}>
              🔐 {lang==="en"?"Enter the 6-digit code from your Authenticator app":"请输入 Authenticator App 的 6 位验证码"}
            </div>
            <input value={totpCode}
              onChange={e => setTotpCode(e.target.value.replace(/\D/g,"").slice(0,6))}
              placeholder="000000" maxLength={6} inputMode="numeric"
              style={{width:"100%",padding:"0.75rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"1.2rem",textAlign:"center",letterSpacing:"0.3rem",fontFamily:"monospace",boxSizing:"border-box"}}/>
          </div>
        )}

        {error && <div style={{color:"#ff4444",background:"#2a1a1a",padding:"0.75rem",borderRadius:"8px",marginBottom:"1rem",border:"1px solid #ff4444"}}>{error}</div>}

        <button onClick={handleSubmit} disabled={loading}
          style={{width:"100%",padding:"0.75rem",background:"#f59e0b",border:"none",borderRadius:"8px",color:"#000",fontWeight:"bold",cursor:"pointer",fontSize:"1rem"}}>
          {loading ? (lang==="en"?"Please wait...":"请稍候...")
            : mode === "register" ? (lang==="en"?"Sign Up":"注册")
            : (lang==="en"?"Log In":"登录")}
        </button>

        {mode === "login" && (
          <div style={{textAlign:"center",marginTop:"0.75rem"}}>
            <Link href="/auth/forgot-password" style={{color:"#666",fontSize:"0.85rem"}}>{lang==="en"?"Forgot password?":"忘记密码？"}</Link>
          </div>
        )}
      </div>
    </div>
  );
}
