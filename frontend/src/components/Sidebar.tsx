"use client";
import LanguageSwitcher from "@/lib/i18n/LanguageSwitcher";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useMemo, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";
import { updateLocalUser } from "@/lib/userState";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface SidebarUser {
  name?: string;
  email?: string;
  credits?: number;
}

export default function Sidebar() {
  const router = useRouter();
  const { t } = useLang();
  // useSyncExternalStore 订阅 localStorage["user"] — 自动跨 tab + 同 tab 同步,
  const userJson = useLocalStorageItem("user");
  const user: SidebarUser | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as SidebarUser; } catch { return null; }
  }, [userJson]);

  // P159(2026-05-06):Sidebar 挂载时 fetch /me 拉真实 credits 覆盖本地缓存
  // 防 admin 后台调账后用户看到旧 credits + 防 adjustLocalUserCredits 局部算误差
  useEffect(() => {
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("token");
    if (!token) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (cancelled || !data || typeof data !== "object") return;
        if (typeof data.credits === "number") {
          updateLocalUser({
            credits: data.credits,
            ...(data.name ? { name: data.name } : {}),
            ...(data.role ? { role: data.role } : {}),
          });
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  if (!user) return null;

  return (
    <aside style={{width:"80px",background:"#fff",borderRight:"1px solid rgba(0,0,0,0.06)",padding:"1.5rem 0.75rem",display:"flex",flexDirection:"column",alignItems:"center",gap:"1rem",height:"100vh",position:"sticky",top:0,flexShrink:0}}>
      <div onClick={()=>router.push("/")} style={{cursor:"pointer",marginBottom:"1rem",fontSize:"1.1rem",fontFamily:"Georgia,serif",fontStyle:"italic",fontWeight:700,color:"#0d0d0d",textAlign:"center"}}>
        xL
      </div>

      <button onClick={()=>router.push("/dashboard")} title={t("sidebar.dashboard")}
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"#f5f3ed",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.2rem",color:"#333",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#e8e4d9";}}
        onMouseLeave={e=>{e.currentTarget.style.background="#f5f3ed";}}>
        ⌂
      </button>

      <button onClick={()=>router.push("/ad-video")} title={t("sidebar.adVideo")}
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.1rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ▶
      </button>

      <button onClick={()=>router.push("/video/replicate")} title="视频复刻(适合服装产品)"
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.2rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ⎘
      </button>

      <button onClick={()=>router.push("/video/extract")} title="视频脚本提取"
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.2rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ⌬
      </button>

      <button onClick={()=>router.push("/video/general")} title="通用产品视频(任意品类)"
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.2rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ✦
      </button>

      <button onClick={()=>router.push("/video-clone-vidu")} title="视频复刻 Vidu Q2 Pro(任意时长 + 切片拼接,推荐)"
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.2rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ⟳
      </button>

      <button onClick={()=>router.push("/pricing")} title={t("sidebar.pricing")}
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.1rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ✦
      </button>

      <div style={{marginTop:"auto",display:"flex",flexDirection:"column",alignItems:"center",gap:"0.5rem"}}>
        <LanguageSwitcher />
        <div style={{fontSize:"0.7rem",color:"#888",textAlign:"center"}}>{user.credits||0}</div>
        <button onClick={()=>router.push("/profile")} title={user.name||user.email}
          style={{width:"40px",height:"40px",borderRadius:"50%",background:"#0d0d0d",color:"#fff",border:"none",cursor:"pointer",fontSize:"0.9rem",fontWeight:500}}>
          {(user.name||user.email||"?").charAt(0).toUpperCase()}
        </button>
      </div>
    </aside>
  );
}
