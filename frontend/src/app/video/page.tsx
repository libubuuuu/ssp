"use client";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

const MODES = [
  { key:"image-to-video", label:"图生视频", desc:"上传首帧，AI 生成动态视频" },
  { key:"element-replace", label:"元素替换", desc:"替换视频中的商品/人物" },
  { key:"remake", label:"翻拍复刻", desc:"提取运镜节奏，换素材翻拍" },
];

export default function VideoPage(){
  const [mode,setMode]=useState("image-to-video");
  const [imageFile,setImageFile]=useState<File|null>(null);
  const [imagePreview,setImagePreview]=useState("");
  const [prompt,setPrompt]=useState("");
  const [duration,setDuration]=useState(5);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);
  const fileRef=useRef<HTMLInputElement>(null);

  useEffect(()=>{
    const saved=localStorage.getItem("video_gallery");
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
  },[]);

  const saveGallery=(g:any[])=>{
    setGallery(g);
    localStorage.setItem("video_gallery",JSON.stringify(g.slice(0,50)));
  };

  const handleFile=(f:File)=>{
    setImageFile(f);
    const reader=new FileReader();
    reader.onload=e=>setImagePreview(e.target?.result as string);
    reader.readAsDataURL(f);
  };

  const uploadFile=async(f:File)=>{
    const token=localStorage.getItem("token")||"";
    const fd=new FormData();
    fd.append("file",f);
    const res=await fetch(`${API_BASE}/api/content/upload`,{
      method:"POST",
      headers:{"Authorization":`Bearer ${token}`},
      body:fd,
    });
    const data=await res.json();
    if(!res.ok)throw new Error(data.detail||"上传失败");
    return data.url||data.image_url;
  };

  const generate=async()=>{
    if(!imageFile){setError("请上传首帧图片");return;}
    setError("");setLoading(true);
    try{
      const token=localStorage.getItem("token")||"";
      let imgUrl="";
      try{imgUrl=await uploadFile(imageFile);}catch(e){imgUrl=imagePreview;}
      const res=await fetch(`${API_BASE}/api/video/image-to-video`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({image_url:imgUrl,prompt,duration_sec:duration}),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"生成失败");
      const url=data.video_url||data.url||data.data?.video_url;
      if(!url)throw new Error("未返回视频");
      saveGallery([{url,prompt:prompt||"图生视频",time:Date.now()},...gallery]);
    }catch(e:any){setError(e.message);}
    finally{setLoading(false);}
  };

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>

      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>视频创作</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>视频<span style={{fontStyle:"italic"}}> 画布</span></h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm("清空画布？")){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>

        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && !loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>▶</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>还没有视频作品，开始你的第一次创作吧</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>在右侧上传首帧图片，点击「开始生成」</div>
            </div>
          )}
          {loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>AI 正在生成视频... (1-3 分钟)</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0 && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:"16/9",boxShadow:"0 4px 12px rgba(0,0,0,0.04)"}}>
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
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>模式</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
            {MODES.map(m=>(
              <button key={m.key} onClick={()=>setMode(m.key)}
                style={{textAlign:"left",padding:"0.7rem 0.9rem",border:mode===m.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:mode===m.key?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{m.label}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>首帧图片</div>
          <input ref={fileRef} type="file" accept="image/*" onChange={e=>{const f=e.target.files?.[0];if(f)handleFile(f);}} style={{display:"none"}}/>
          {imagePreview ? (
            <div onClick={()=>fileRef.current?.click()} style={{position:"relative",cursor:"pointer",borderRadius:"12px",overflow:"hidden",aspectRatio:"16/9",background:"#fafaf7"}}>
              <img src={imagePreview} alt="" style={{width:"100%",height:"100%",objectFit:"cover"}}/>
              <div style={{position:"absolute",inset:0,background:"rgba(0,0,0,0.5)",display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontSize:"0.85rem",opacity:0,transition:"opacity 0.2s"}} onMouseEnter={e=>e.currentTarget.style.opacity="1"} onMouseLeave={e=>e.currentTarget.style.opacity="0"}>点击更换</div>
            </div>
          ) : (
            <button onClick={()=>fileRef.current?.click()} style={{width:"100%",padding:"1.5rem 0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#888",fontSize:"0.85rem",display:"flex",flexDirection:"column",alignItems:"center",gap:"0.5rem"}}>
              <span style={{fontSize:"1.5rem",color:"#bbb"}}>↑</span>
              点击上传图片
            </button>
          )}
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>时长</div>
          <select value={duration} onChange={e=>setDuration(parseInt(e.target.value))} style={{width:"100%",padding:"0.65rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.85rem",background:"#fff !important",color:"#333 !important"}}>
            <option value="5">5 秒</option>
            <option value="10">10 秒</option>
          </select>
        </div>

        <div style={{flex:1,display:"flex",flexDirection:"column"}}>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>运动描述（可选）</div>
          <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="描述视频中期望的运动效果..." 
            style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"100px",resize:"vertical",fontFamily:"inherit",background:"#fff !important",color:"#333 !important",flex:1}}/>
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
