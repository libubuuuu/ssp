"use client";
import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";

const MENU = [
  { group: "创作", items: [
    { key: "image", label: "图片生成", icon: "◧" },
    { key: "video", label: "视频生成", icon: "▶" },
    { key: "avatar", label: "数字人", icon: "◉" },
    { key: "voice-clone", label: "语音克隆", icon: "◐" },
  ]},
  { group: "管理", items: [
    { key: "tasks/history", label: "任务历史", icon: "☰" },
    { key: "pricing", label: "充值中心", icon: "✦" },
    { key: "profile", label: "个人中心", icon: "◯" },
  ]},
];

export default function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    const u = localStorage.getItem("user");
    if (u) try { setUser(JSON.parse(u)); } catch {}
  }, []);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    router.push("/");
  };

  if (!user) return null;

  return (
    <aside style={{width:"240px",background:"#fff",borderRight:"1px solid rgba(0,0,0,0.06)",padding:"2rem 1rem",display:"flex",flexDirection:"column",height:"100vh",position:"sticky",top:0,flexShrink:0}}>
      <div onClick={()=>router.push("/dashboard")} style={{padding:"0 1rem 2rem",cursor:"pointer"}}>
        <span style={{fontSize:"1.2rem",fontFamily:"Georgia,serif",fontStyle:"italic",fontWeight:700,color:"#0d0d0d"}}>xiaoLi ai.</span>
      </div>
      {MENU.map((grp,gi)=>(
        <div key={gi} style={{marginBottom:"1.5rem"}}>
          <div style={{fontSize:"0.7rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",padding:"0 1rem",marginBottom:"0.5rem"}}>{grp.group}</div>
          {grp.items.map(it=>{
            const active = pathname?.startsWith("/"+it.key);
            return (
              <button key={it.key} onClick={()=>router.push("/"+it.key)}
                style={{display:"flex",alignItems:"center",gap:"0.75rem",width:"100%",padding:"0.7rem 1rem",background:active?"#f5f3ed":"none",border:"none",borderRadius:"10px",cursor:"pointer",color:"#333",fontSize:"0.9rem",textAlign:"left",marginBottom:"2px",transition:"background 0.15s"}}>
                <span style={{fontSize:"1.1rem",color:"#666"}}>{it.icon}</span>
                {it.label}
              </button>
            );
          })}
        </div>
      ))}
      <div style={{marginTop:"auto",padding:"1rem",borderTop:"1px solid #eee",display:"flex",alignItems:"center",gap:"0.75rem"}}>
        <div style={{width:"36px",height:"36px",borderRadius:"50%",background:"#0d0d0d",color:"#fff",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"0.9rem",fontWeight:500}}>{(user.name||user.email||"?").charAt(0).toUpperCase()}</div>
        <div style={{flex:1,minWidth:0}}>
          <div style={{fontSize:"0.85rem",color:"#333",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{user.name||user.email}</div>
          <div style={{fontSize:"0.75rem",color:"#888"}}>{user.credits||0} 积分</div>
        </div>
        <button onClick={logout} style={{background:"none",border:"none",color:"#999",cursor:"pointer",fontSize:"0.8rem"}}>退出</button>
      </div>
    </aside>
  );
}
