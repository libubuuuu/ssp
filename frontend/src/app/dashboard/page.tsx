"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const FEATURES = [
  { key:"image", label:"图片生成", desc:"文生图 · 图生图 · 多参考图", icon:"◧", color:"#f0e8d5" },
  { key:"video", label:"视频生成", desc:"图生视频 · 元素替换 · 翻拍", icon:"▶", color:"#e5e0d0" },
  { key:"video/studio", label:"长视频工作台", desc:"上传视频 · 自动拆分 · 批量翻拍 · 拼接", icon:"▦", color:"#ead8c0" },
  { key:"avatar", label:"数字人", desc:"口型同步 · 无多余动作", icon:"◉", color:"#ede5d3" },
  { key:"voice-clone", label:"语音克隆", desc:"5-10 秒提取音色", icon:"◐", color:"#e8e2d0" },
  { key:"tasks/history", label:"任务历史", desc:"查看所有生成记录", icon:"☰", color:"#f2ece0" },
  { key:"pricing", label:"充值中心", desc:"积分套餐与充值", icon:"✦", color:"#ebe5d5" },
];

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<any>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const u = localStorage.getItem("user");
    if (!token || !u) { router.push("/auth"); return; }
    try { setUser(JSON.parse(u)); } catch {}
  }, [router]);

  if (!user) return <div style={{minHeight:"100vh",background:"#edeae4"}}/>;

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"3rem 4rem",overflowY:"auto"}}>
        <div style={{marginBottom:"3rem"}}>
          <div style={{fontSize:"0.9rem",color:"#888",marginBottom:"0.5rem"}}>欢迎回来</div>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{user.name||user.email.split("@")[0]},</h1>
          <h1 style={{fontSize:"2.4rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif",fontStyle:"italic"}}>今天想创作什么？</h1>
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
