"use client";
import { useLang } from "@/lib/i18n/LanguageContext";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function AuthPage() {
  const { lang } = useLang();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [totpCode, setTotpCode] = useState("");
  const [need2FA, setNeed2FA] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    setError(""); setLoading(true);
    try {
      const url = mode === "login" ? `${API_BASE}/api/auth/login` : `${API_BASE}/api/auth/register`;
      const body = mode === "login" ? { ...form, totp_code: totpCode } : form;
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        // 检查是否需要 2FA
        if (typeof data.detail === "object" && data.detail?.need_2fa) {
          setNeed2FA(true);
          setError("");
          return;
        }
        throw new Error(typeof data.detail === "string" ? data.detail : "请求失败");
      }
      localStorage.setItem("token", data.token);
      localStorage.setItem("user", JSON.stringify(data.user));
      setNeed2FA(false);
      setTotpCode("");
      router.push("/");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{minHeight:"100vh",background:"#0a0a0a",display:"flex",alignItems:"center",justifyContent:"center"}}>
      <div style={{background:"#1a1a1a",padding:"2rem",borderRadius:"12px",width:"100%",maxWidth:"400px"}}>
        <h2 style={{color:"#fff",textAlign:"center",marginBottom:"0.5rem"}}>{mode === "login" ? (lang==="en"?"Log In":"登录") : (lang==="en"?"Sign Up":"注册")}</h2>
        <p style={{color:"#888",textAlign:"center",marginBottom:"1.5rem",fontSize:"0.9rem"}}>
          {mode === "register" ? (lang==="en"?"Create account, get 100 free credits":"创建新账号，赠送 100 积分") : (lang==="en"?"Welcome back":"欢迎回来")}
        </p>
        {mode === "register" && (
          <input placeholder="昵称" value={form.name}
            onChange={e => setForm({...form, name: e.target.value})}
            style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        )}
        <input placeholder={lang==="en"?"Email":"邮箱"} value={form.email}
          onChange={e => setForm({...form, email: e.target.value})}
          style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        <input type="password" placeholder={lang==="en"?"Password":"密码"} value={form.password}
          onChange={e => setForm({...form, password: e.target.value})}
          style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        {need2FA && (
          <div style={{marginBottom:"1rem",padding:"0.75rem",background:"#1a2a1a",border:"1px solid #0a7",borderRadius:"8px"}}>
            <div style={{color:"#0a7",fontSize:"0.85rem",marginBottom:"0.5rem"}}>
              🔐 {lang==="en"?"Enter the 6-digit code from your Authenticator app":"请输入 Authenticator App 的 6 位验证码"}
            </div>
            <input value={totpCode}
              onChange={e => setTotpCode(e.target.value.replace(/\D/g,"").slice(0,6))}
              placeholder="000000" maxLength={6}
              style={{width:"100%",padding:"0.75rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",fontSize:"1.2rem",textAlign:"center",letterSpacing:"0.3rem",fontFamily:"monospace",boxSizing:"border-box"}}/>
          </div>
        )}
        {error && <div style={{color:"#ff4444",background:"#2a1a1a",padding:"0.75rem",borderRadius:"8px",marginBottom:"1rem",border:"1px solid #ff4444"}}>{error}</div>}
        <button onClick={handleSubmit} disabled={loading}
          style={{width:"100%",padding:"0.75rem",background:"#f59e0b",border:"none",borderRadius:"8px",color:"#000",fontWeight:"bold",cursor:"pointer",fontSize:"1rem"}}>
          {loading ? (lang==="en"?"Please wait...":"请稍候...") : mode === "login" ? (lang==="en"?"Log In":"登录") : (lang==="en"?"Sign Up":"注册")}
        </button>
        <div style={{textAlign:"center",marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>
          {mode === "login" ? (
            <span>{lang==="en"?"No account? ":"没有账号？"}<span onClick={() => setMode("register")} style={{color:"#f59e0b",cursor:"pointer"}}>{lang==="en"?"Sign up":"去注册"}</span></span>
          ) : (
            <span>{lang==="en"?"Have an account? ":"已有账号？"}<span onClick={() => setMode("login")} style={{color:"#f59e0b",cursor:"pointer"}}>{lang==="en"?"Log in":"去登录"}</span></span>
          )}
        </div>
        {mode === "login" && (
          <div style={{textAlign:"center",marginTop:"0.5rem"}}>
            <Link href="/auth/forgot-password" style={{color:"#666",fontSize:"0.85rem"}}>{lang==="en"?"Forgot password?":"忘记密码？"}</Link>
          </div>
        )}
      </div>
    </div>
  );
}
