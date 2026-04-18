"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const MENU = [
  { group: "创作", items: [
    { key: "image", label: "图片生成", desc: "文生图 · 图生图 · 多参考图", icon: "◧" },
    { key: "video", label: "视频生成", desc: "图生视频 · 元素替换 · 翻拍", icon: "▶" },
    { key: "avatar", label: "数字人", desc: "口型同步 · 无多余动作", icon: "◉" },
    { key: "voice-clone", label: "语音克隆", desc: "5-10秒提取音色", icon: "◐" },
  ]},
  { group: "管理", items: [
    { key: "tasks/history", label: "任务历史", desc: "查看所有生成记录", icon: "☰" },
    { key: "pricing", label: "充值中心", desc: "积分套餐", icon: "✦" },
    { key: "profile", label: "个人中心", desc: "账号设置", icon: "◯" },
  ]},
];

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<any>(null);
  const [active, setActive] = useState("overview");

  useEffect(() => {
    const token = localStorage.getItem("token");
    const u = localStorage.getItem("user");
    if (!token || !u) { router.push("/auth"); return; }
    try { setUser(JSON.parse(u)); } catch {}
  }, [router]);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    router.push("/");
  };

  if (!user) return <div style={{minHeight:"100vh",background:"#edeae4"}}/>;

  return (
    <div style={{minHeight:"100vh",background:"#edeae4",display:"flex",fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"}}>
      <aside style={{width:"260px",background:"#fff",borderRight:"1px solid rgba(0,0,0,0.06)",padding:"2rem 1rem",display:"flex",flexDirection:"column",minHeight:"100vh"}}>
        <div onClick={()=>router.push("/")} style={{padding:"0 1rem 2rem",cursor:"pointer"}}>
          <span style={{fontSize:"1.2rem",fontFamily:"Georgia,serif",fontStyle:"italic",fontWeight:700,color:"#0d0d0d"}}>xiaoLi ai.</span>
        </div>
        {MENU.map((grp,gi)=>(
          <div key={gi} style={{marginBottom:"1.5rem"}}>
            <div style={{fontSize:"0.7rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",padding:"0 1rem",marginBottom:"0.5rem"}}>{grp.group}</div>
            {grp.items.map(it=>(
              <button key={it.key} onClick={()=>{setActive(it.key);router.push("/"+it.key);}}
                style={{display:"flex",alignItems:"center",gap:"0.75rem",width:"100%",padding:"0.7rem 1rem",background:active===it.key?"#f5f3ed":"none",border:"none",borderRadius:"10px",cursor:"pointer",color:"#333",fontSize:"0.9rem",textAlign:"left",marginBottom:"2px",transition:"background 0.15s"}}
                onMouseEnter={e=>{if(active!==it.key)e.currentTarget.style.background="#f9f7f2";}}
                onMouseLeave={e=>{if(active!==it.key)e.currentTarget.style.background="none";}}>
                <span style={{fontSize:"1.1rem",color:"#666"}}>{it.icon}</span>
                {it.label}
              </button>
            ))}
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

      <main style={{flex:1,padding:"3rem 4rem",overflowY:"auto"}}>
        <div style={{marginBottom:"2.5rem"}}>
          <div style={{fontSize:"0.9rem",color:"#888",marginBottom:"0.5rem"}}>欢迎回来</div>
          <h1 style={{fontSize:"2.2rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{user.name||user.email.split("@")[0]},</h1>
          <h1 style={{fontSize:"2.2rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif",fontStyle:"italic"}}>今天想创作什么？</h1>
        </div>

        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:"1.25rem",marginBottom:"3rem"}}>
          {MENU[0].items.map(it=>(
            <div key={it.key} onClick={()=>router.push("/"+it.key)}
              style={{background:"#fff",borderRadius:"16px",padding:"1.75rem",cursor:"pointer",border:"1px solid rgba(0,0,0,0.04)",transition:"all 0.25s"}}
              onMouseEnter={e=>{e.currentTarget.style.transform="translateY(-4px)";e.currentTarget.style.boxShadow="0 12px 30px rgba(0,0,0,0.08)";}}
              onMouseLeave={e=>{e.currentTarget.style.transform="translateY(0)";e.currentTarget.style.boxShadow="none";}}>
              <div style={{fontSize:"1.8rem",color:"#0d0d0d",marginBottom:"1rem"}}>{it.icon}</div>
              <div style={{fontSize:"1.1rem",color:"#0d0d0d",marginBottom:"0.4rem",fontWeight:500}}>{it.label}</div>
              <div style={{fontSize:"0.85rem",color:"#888",lineHeight:1.5}}>{it.desc}</div>
            </div>
          ))}
        </div>

        <div style={{background:"#fff",borderRadius:"16px",padding:"1.75rem",border:"1px solid rgba(0,0,0,0.04)"}}>
          <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"1rem"}}>账户概况</div>
          <div style={{display:"flex",gap:"3rem"}}>
            <div>
              <div style={{fontSize:"2rem",fontWeight:300,color:"#0d0d0d"}}>{user.credits||0}</div>
              <div style={{fontSize:"0.8rem",color:"#888"}}>可用积分</div>
            </div>
            <div>
              <div style={{fontSize:"2rem",fontWeight:300,color:"#0d0d0d"}}>{user.role==="admin"?"管理员":"普通用户"}</div>
              <div style={{fontSize:"0.8rem",color:"#888"}}>账户类型</div>
            </div>
            <button onClick={()=>router.push("/pricing")} style={{marginLeft:"auto",alignSelf:"center",background:"#0d0d0d",color:"#fff",border:"none",padding:"0.75rem 1.5rem",borderRadius:"999px",cursor:"pointer",fontSize:"0.9rem"}}>充值积分 →</button>
          </div>
        </div>
      </main>
    </div>
  );
}
