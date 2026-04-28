"use client";
import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

const FEATURE_KEYS = [
  { key:"image", i18nKey:"image", icon:"◧", color:"#f0e8d5" },
  { key:"video", i18nKey:"video", icon:"▶", color:"#e5e0d0" },
  { key:"video/studio", i18nKey:"studio", icon:"▦", color:"#ead8c0" },
  { key:"avatar", i18nKey:"avatar", icon:"◉", color:"#ede5d3" },
  { key:"voice-clone", i18nKey:"voiceClone", icon:"◐", color:"#e8e2d0" },
  { key:"tasks/history", i18nKey:"history", icon:"☰", color:"#f2ece0" },
  { key:"pricing", i18nKey:"pricing", icon:"✦", color:"#ebe5d5" },
];

interface DashboardUser {
  name?: string;
  email?: string;
}

export default function Dashboard() {
  const router = useRouter();
  const { t, lang } = useLang();
  const token = useLocalStorageItem("token");
  const userJson = useLocalStorageItem("user");
  const user: DashboardUser | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as DashboardUser; } catch { return null; }
  }, [userJson]);
  const FEATURES = FEATURE_KEYS.map(f => ({
    ...f,
    label: t(`dashboard.features.${f.i18nKey}.label`),
    desc: t(`dashboard.features.${f.i18nKey}.desc`),
  }));

  // 未登录 → 跳 /auth(用 effect 因为 router.push 在 render 期不允许)
  useEffect(() => {
    if (!token || !userJson) router.push("/auth");
  }, [token, userJson, router]);

  if (!user) return <div style={{minHeight:"100vh",background:"#edeae4"}}/>;

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"3rem 4rem",overflowY:"auto"}}>
        <div style={{marginBottom:"3rem"}}>
          <div style={{fontSize:"0.9rem",color:"#888",marginBottom:"0.5rem"}}>{t("dashboard.welcomeBack")}</div>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{user.name||user.email.split("@")[0]},</h1>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif",fontStyle:"italic"}}>{t("dashboard.todayCreate")}</h1>
        </div>

        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"1.25rem"}}>
          {FEATURES.map(f=>(
            <div key={f.key} onClick={()=>router.push("/"+f.key)}
              style={{background:"#fff",borderRadius:"20px",padding:"2rem",cursor:"pointer",border:"1px solid rgba(0,0,0,0.04)",transition:"all 0.25s",minHeight:"180px",display:"flex",flexDirection:"column",justifyContent:"space-between"}}
              onMouseEnter={e=>{e.currentTarget.style.transform="translateY(-4px)";e.currentTarget.style.boxShadow="0 16px 40px rgba(0,0,0,0.08)";}}
              onMouseLeave={e=>{e.currentTarget.style.transform="translateY(0)";e.currentTarget.style.boxShadow="none";}}>
              <div style={{width:"52px",height:"52px",borderRadius:"14px",background:f.color,display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.6rem",color:"#0d0d0d"}}>{f.icon}</div>
              <div>
                <div style={{fontSize:"1.15rem",color:"#0d0d0d",marginBottom:"0.4rem",fontWeight:500}}>{f.label}</div>
                <div style={{fontSize:"0.85rem",color:"#888",lineHeight:1.5}}>{f.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
