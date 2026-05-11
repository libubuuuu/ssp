"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import FeatureShowcase from "@/components/FeatureShowcase";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

const ONBOARDING_FLAG = "onboarding_showcase_v1_shown";

const FEATURE_KEYS = [
  { key:"video/general", i18nKey:"general", icon:"✦", color:"#fce7e7", labelFallback:"通用产品视频", descFallback:"任意品类·多图参考·真人模特" },
  { key:"video/studio", i18nKey:"studio", icon:"▦", color:"#ead8c0" },
  { key:"tasks/history", i18nKey:"history", icon:"☰", color:"#f2ece0" },
  { key:"pricing", i18nKey:"pricing", icon:"✦", color:"#ebe5d5" },
];

interface DashboardUser {
  name?: string;
  email?: string;
}

export default function Dashboard() {
  const router = useRouter();
  const { t } = useLang();
  const token = useLocalStorageItem("token");
  const userJson = useLocalStorageItem("user");
  const user: DashboardUser | null = useMemo(() => {
    if (!userJson) return null;
    try { return JSON.parse(userJson) as DashboardUser; } catch { return null; }
  }, [userJson]);
  const FEATURES = FEATURE_KEYS.map(f => {
    const labelKey = `dashboard.features.${f.i18nKey}.label`;
    const descKey = `dashboard.features.${f.i18nKey}.desc`;
    const labelT = t(labelKey);
    const descT = t(descKey);
    // 缺 i18n 时 fallback 到 hardcoded 中文
    const label = labelT === labelKey && (f as { labelFallback?: string }).labelFallback ? (f as { labelFallback?: string }).labelFallback! : labelT;
    const desc = descT === descKey && (f as { descFallback?: string }).descFallback ? (f as { descFallback?: string }).descFallback! : descT;
    return { ...f, label, desc };
  });

  const [showPopup, setShowPopup] = useState(false);

  // 未登录 → 跳 /auth(用 effect 因为 router.push 在 render 期不允许)
  useEffect(() => {
    if (!token || !userJson) router.push("/auth");
  }, [token, userJson, router]);

  // 首次进 dashboard 弹一次 onboarding popup,关掉后写 localStorage 不再弹
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!token || !userJson) return;
    if (localStorage.getItem(ONBOARDING_FLAG) === "1") return;
    setShowPopup(true);
  }, [token, userJson]);

  const dismissPopup = () => {
    setShowPopup(false);
    if (typeof window !== "undefined") localStorage.setItem(ONBOARDING_FLAG, "1");
  };

  if (!user) return <div style={{minHeight:"100vh",background:"#edeae4"}}/>;

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"3rem 4rem",overflowY:"auto",maxWidth:"1280px",width:"100%",margin:"0 auto"}}>
        <div style={{marginBottom:"2rem"}}>
          <div style={{fontSize:"0.9rem",color:"#888",marginBottom:"0.5rem"}}>{t("dashboard.welcomeBack")}</div>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{user.name||user.email.split("@")[0]},</h1>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif",fontStyle:"italic"}}>{t("dashboard.todayCreate")}</h1>
        </div>

        <FeatureShowcase mode="embedded" />

        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(340px,1fr))",gap:"1.5rem"}}>
          {FEATURES.map(f=>(
            <div key={f.key} onClick={()=>router.push("/"+f.key)}
              style={{background:"#fff",borderRadius:"20px",padding:"2.5rem",cursor:"pointer",border:"1px solid rgba(0,0,0,0.04)",transition:"all 0.25s",minHeight:"220px",display:"flex",flexDirection:"column",justifyContent:"space-between"}}
              onMouseEnter={e=>{e.currentTarget.style.transform="translateY(-4px)";e.currentTarget.style.boxShadow="0 16px 40px rgba(0,0,0,0.08)";}}
              onMouseLeave={e=>{e.currentTarget.style.transform="translateY(0)";e.currentTarget.style.boxShadow="none";}}>
              <div style={{width:"64px",height:"64px",borderRadius:"16px",background:f.color,display:"flex",alignItems:"center",justifyContent:"center",fontSize:"2rem",color:"#0d0d0d"}}>{f.icon}</div>
              <div>
                <div style={{fontSize:"1.3rem",color:"#0d0d0d",marginBottom:"0.5rem",fontWeight:500}}>{f.label}</div>
                <div style={{fontSize:"0.95rem",color:"#888",lineHeight:1.5}}>{f.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </main>
      {showPopup && <FeatureShowcase mode="popup" onClose={dismissPopup} />}
    </div>
  );
}
