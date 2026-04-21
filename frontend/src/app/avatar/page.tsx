"use client";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://ailixiao.com";

export default function AvatarPage(){
  const [image,setImage]=useState<File|null>(null);
  const [audio,setAudio]=useState<File|null>(null);
  const [model,setModel]=useState("hunyuan-avatar");
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);
  const imgRef=useRef<HTMLInputElement>(null);
  const audRef=useRef<HTMLInputElement>(null);

  useEffect(()=>{
    const saved=localStorage.getItem("avatar_gallery");
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
  },[]);

  const saveGallery=(g:any[])=>{
    setGallery(g);
    localStorage.setItem("avatar_gallery",JSON.stringify(g.slice(0,50)));
  };

  const generate=async()=>{
    if(!image||!audio){setError("请上传图片和音频");return;}
    setError("");setLoading(true);
    try{
      const token=localStorage.getItem("token")||"";
      const fd=new FormData();
      fd.append("image",image);
      fd.append("audio",audio);
      fd.append("model",model);
      const res=await fetch(`${API_BASE}/api/digital-human/generate`,{
        method:"POST",
        headers:{"Authorization":`Bearer ${token}`},
        body:fd,
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"生成失败");
      if(!data.video_url)throw new Error("未返回视频");
      saveGallery([{url:data.video_url,prompt:`${model} · 数字人`,time:Date.now()},...gallery]);
    }catch(e:any){setError(e.message);}
    finally{setLoading(false);}
  };

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>

      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>数字人</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>数字人<span style={{fontStyle:"italic"}}> 画布</span></h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm("清空画布？")){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>

        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && !loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>◉</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>还没有数字人作品，开始你的第一次创作吧</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>上传人物图片和音频，点击「开始生成」</div>
            </div>
          )}
          {loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>AI 正在驱动口型... (1-3 分钟)</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0 && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:"9/16",boxShadow:"0 4px 12px rgba(0,0,0,0.04)"}}>
                  <video src={item.url} controls style={{width:"100%",height:"100%",objectFit:"cover"}}/>
                  <div style={{position:"absolute",bottom:0,left:0,right:0,padding:"0.75rem",background:"linear-gradient(transparent,rgba(0,0,0,0.75))",color:"#fff",fontSize:"0.75rem",pointerEvents:"none"}}>{(item.prompt||"").slice(0,40)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>

      <aside style={{width:"340px",background:"#fff",borderLeft:"1px solid rgba(0,0,0,0.06)",padding:"2rem 1.75rem",display:"flex",flexDirection:"column",gap:"1.25rem",height:"100vh",position:"sticky",top:0,overflowY:"auto"}}>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>模型</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
            {[
              {k:"hunyuan-avatar",l:"腾讯混元",d:"高质量口型驱动"},
              {k:"pixverse-lipsync",l:"Pixverse",d:"速度快，适合预览"}
            ].map(m=>(
              <button key={m.k} onClick={()=>setModel(m.k)}
                style={{textAlign:"left",padding:"0.7rem 0.9rem",border:model===m.k?"2px solid #0d0d0d":"1px solid #e5e5e5",background:model===m.k?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{m.l}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{m.d}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>人物图片</div>
          <input ref={imgRef} type="file" accept="image/*" onChange={e=>setImage(e.target.files?.[0]||null)} style={{display:"none"}}/>
          <button onClick={()=>imgRef.current?.click()} style={{width:"100%",padding:"0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#666",fontSize:"0.85rem"}}>
            {image?`✓ ${image.name}`:"点击上传图片"}
          </button>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>音频文件</div>
          <input ref={audRef} type="file" accept="audio/*" onChange={e=>setAudio(e.target.files?.[0]||null)} style={{display:"none"}}/>
          <button onClick={()=>audRef.current?.click()} style={{width:"100%",padding:"0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#666",fontSize:"0.85rem"}}>
            {audio?`✓ ${audio.name}`:"点击上传音频"}
          </button>
        </div>

        <div style={{flex:1}}/>

        {error && <div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}

        <button onClick={generate} disabled={loading}
          style={{padding:"0.9rem",background:loading?"#999":"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {loading?"生成中...":"开始生成"}
        </button>
      </aside>
    </div>
  );
}
