"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import FeatureShowcase from "@/components/FeatureShowcase";
import DashboardCarousel from "@/components/DashboardCarousel";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useLocalStorageItem } from "@/lib/hooks/useLocalStorageItem";

const ONBOARDING_FLAG = "onboarding_showcase_v1_shown";

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

        <DashboardCarousel />
        <FeatureShowcase mode="embedded" />
      </main>
      {showPopup && <FeatureShowcase mode="popup" onClose={dismissPopup} />}
    </div>
  );
}
