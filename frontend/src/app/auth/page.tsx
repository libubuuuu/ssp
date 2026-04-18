"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [form, setForm] = useState({ email: "", password: "", name: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    setError(""); setLoading(true);
    try {
      const url = mode === "login" ? `${API_BASE}/api/auth/login` : `${API_BASE}/api/auth/register`;
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "请求失败");
      localStorage.setItem("token", data.token);
      localStorage.setItem("user", JSON.stringify(data.user));
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
        <h2 style={{color:"#fff",textAlign:"center",marginBottom:"0.5rem"}}>{mode === "login" ? "登录" : "注册"}</h2>
        <p style={{color:"#888",textAlign:"center",marginBottom:"1.5rem",fontSize:"0.9rem"}}>
          {mode === "register" ? "创建新账号，赠送 100 积分" : "欢迎回来"}
        </p>
        {mode === "register" && (
          <input placeholder="昵称" value={form.name}
            onChange={e => setForm({...form, name: e.target.value})}
            style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        )}
        <input placeholder="邮箱" value={form.email}
          onChange={e => setForm({...form, email: e.target.value})}
          style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        <input type="password" placeholder="密码" value={form.password}
          onChange={e => setForm({...form, password: e.target.value})}
          style={{width:"100%",padding:"0.75rem",marginBottom:"1rem",background:"#2a2a2a",border:"1px solid #333",borderRadius:"8px",color:"#fff",boxSizing:"border-box"}}/>
        {error && <div style={{color:"#ff4444",background:"#2a1a1a",padding:"0.75rem",borderRadius:"8px",marginBottom:"1rem",border:"1px solid #ff4444"}}>{error}</div>}
        <button onClick={handleSubmit} disabled={loading}
          style={{width:"100%",padding:"0.75rem",background:"#f59e0b",border:"none",borderRadius:"8px",color:"#000",fontWeight:"bold",cursor:"pointer",fontSize:"1rem"}}>
          {loading ? "请稍候..." : mode === "login" ? "登录" : "注册"}
        </button>
        <div style={{textAlign:"center",marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>
          {mode === "login" ? (
            <span>没有账号？<span onClick={() => setMode("register")} style={{color:"#f59e0b",cursor:"pointer"}}>去注册</span></span>
          ) : (
            <span>已有账号？<span onClick={() => setMode("login")} style={{color:"#f59e0b",cursor:"pointer"}}>去登录</span></span>
          )}
        </div>
        {mode === "login" && (
          <div style={{textAlign:"center",marginTop:"0.5rem"}}>
            <Link href="/auth/forgot-password" style={{color:"#666",fontSize:"0.85rem"}}>忘记密码？</Link>
          </div>
        )}
      </div>
    </div>
  );
}
