"use client";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

const STYLES = [
  { key:"advertising", label:"广告视觉" },
  { key:"minimalist", label:"精致简约" },
  { key:"custom", label:"仅提示词" },
];

const MODELS = [
  { key:"nano-banana-2", label:"经济模式", desc:"最低成本，速度较慢" },
  { key:"flux/schnell", label:"快速模式", desc:"生成速度快，质量高" },
  { key:"flux/dev", label:"专业模式", desc:"更高质量的生成效果" },
];

export default function ImagePage(){
  const [prompt,setPrompt]=useState("");
  const [style,setStyle]=useState("advertising");
  const [model,setModel]=useState("nano-banana-2");
  const [size,setSize]=useState("1024x1024");
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);

  useEffect(()=>{
    const saved=localStorage.getItem("img_gallery");
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
  },[]);

  const saveGallery=(g:any[])=>{
    setGallery(g);
    localStorage.setItem("img_gallery",JSON.stringify(g.slice(0,50)));
  };

  const generate=async()=>{
    if(!prompt.trim()){setError("请输入提示词");return;}
    setError("");setLoading(true);
    try{
      const token=localStorage.getItem("token")||"";
      const res=await fetch(`${API_BASE}/api/image/style`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({prompt,style,model,size}),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"生成失败");
      const url=data.image_url||data.url||data.data?.image_url;
      if(!url)throw new Error("未返回图片");
      saveGallery([{url,prompt,time:Date.now()},...gallery]);
    }catch(e:any){setError(e.message);}
    finally{setLoading(false);}
  };

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>

      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>图片创作</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>我的<span style={{fontStyle:"italic"}}> 画布</span></h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm("清空画布？")){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>

        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && !loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>◧</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>还没有作品，开始你的第一次创作吧</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>在右侧输入提示词，点击「开始生成」</div>
            </div>
          )}
          {loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>AI 正在为您创作...</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0 && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:"1",boxShadow:"0 4px 12px rgba(0,0,0,0.04)"}}>
                  <img src={item.url} alt="" style={{width:"100%",height:"100%",objectFit:"cover"}}/>
                  <div style={{position:"absolute",bottom:0,left:0,right:0,padding:"0.75rem",background:"linear-gradient(transparent,rgba(0,0,0,0.75))",color:"#fff",fontSize:"0.75rem"}}>{(item.prompt||"").slice(0,40)}{(item.prompt||"").length>40?"...":""}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      <aside style={{width:"340px",background:"#fff",borderLeft:"1px solid rgba(0,0,0,0.06)",padding:"2rem 1.75rem",display:"flex",flexDirection:"column",gap:"1.25rem",height:"100vh",position:"sticky",top:0,overflowY:"auto"}}>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>模式</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
            {MODELS.map(m=>(
              <button key={m.key} onClick={()=>setModel(m.key)}
                style={{textAlign:"left",padding:"0.7rem 0.9rem",border:model===m.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:model===m.key?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{m.label}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>风格</div>
          <div style={{display:"flex",gap:"0.4rem",flexWrap:"wrap"}}>
            {STYLES.map(s=>(
              <button key={s.key} onClick={()=>setStyle(s.key)}
                style={{padding:"0.45rem 0.9rem",border:style===s.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:style===s.key?"#f9f7f2":"#fff",borderRadius:"999px",cursor:"pointer",fontSize:"0.8rem",color:"#333"}}>
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>尺寸</div>
          <select value={size} onChange={e=>setSize(e.target.value)} style={{width:"100%",padding:"0.65rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.85rem",background:"#fff !important",color:"#333 !important"}}>
            <option value="1024x1024">正方形 1:1</option>
            <option value="768x1024">竖版 3:4</option>
            <option value="1024x768">横版 4:3</option>
          </select>
        </div>

        <div style={{flex:1,display:"flex",flexDirection:"column"}}>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>提示词</div>
          <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="描述你想要的图片..." 
            style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"120px",resize:"vertical",fontFamily:"inherit",background:"#fff !important",color:"#333 !important",flex:1}}/>
        </div>

        {error && <div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}

        <button onClick={generate} disabled={loading}
          style={{padding:"0.9rem",background:loading?"#999":"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {loading?"生成中...":"开始生成"}
        </button>
      </aside>
    </div>
  );
}
