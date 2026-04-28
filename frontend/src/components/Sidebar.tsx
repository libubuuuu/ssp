"use client";
import LanguageSwitcher from "@/lib/i18n/LanguageSwitcher";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

interface SidebarUser {
  name?: string;
  email?: string;
  credits?: number;
}

export default function Sidebar() {
  const router = useRouter();
  const { t } = useLang();
  // useSyncExternalStore 订阅 localStorage["user"] — 自动跨 tab + 同 tab 同步,
  // 不再 useEffect + 手挂 listener(原 623ceac 的修法,这一版统一收口)
  const userJson = useLocalStorageItem("user");
  const user: SidebarUser | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as SidebarUser; } catch { return null; }
  }, [userJson]);

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

      <button onClick={()=>router.push("/quick-ad")} title={t("sidebar.quickAd")}
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.1rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        ⚡
      </button>

      <button onClick={()=>router.push("/video/oral-broadcast")} title={t("sidebar.oralBroadcast")}
        style={{width:"48px",height:"48px",borderRadius:"12px",border:"none",background:"none",cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.1rem",color:"#666",transition:"all 0.15s"}}
        onMouseEnter={e=>{e.currentTarget.style.background="#f9f7f2";}}
        onMouseLeave={e=>{e.currentTarget.style.background="none";}}>
        🎤
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
