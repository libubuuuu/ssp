"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function AvatarPage(){
  const { t } = useLang();
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
      const auth={"Authorization":`Bearer ${token}`};

      // 1. 上传图片拿 URL
      const fdImg=new FormData();fdImg.append("file",image);
      const rImg=await fetch(`${API_BASE}/api/video/upload/image`,{method:"POST",headers:auth,body:fdImg});
      const dImg=await rImg.json();
      if(!rImg.ok||!dImg.url)throw new Error(dImg.detail||"图片上传失败");

      // 2. 上传音频拿 URL(复用 video upload,fal_client 不区分类型)
      const fdAud=new FormData();fdAud.append("file",audio);
      const rAud=await fetch(`${API_BASE}/api/video/upload/video`,{method:"POST",headers:auth,body:fdAud});
      const dAud=await rAud.json();
      if(!rAud.ok||!dAud.url)throw new Error(dAud.detail||"音频上传失败");

      // 3. 提交真正的数字人生成(扣费在此步,失败自动返还)
      const rGen=await fetch(`${API_BASE}/api/avatar/generate`,{
        method:"POST",
        headers:{...auth,"Content-Type":"application/json"},
        body:JSON.stringify({character_image_url:dImg.url,audio_url:dAud.url,model}),
      });
      const data=await rGen.json();
      if(!rGen.ok)throw new Error(data.detail||t("errors.generationFailed"));
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
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>{t("avatar.title")}</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{t("avatar.title")}<span style={{fontStyle:"italic"}}> 画布</span></h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm(t("confirms.clearCanvas"))){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>

        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && !loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>◉</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>{t("avatar.emptyWorks")}</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>{t("avatar.emptyTip")}</div>
            </div>
          )}
          {loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>{t("avatar.generating")}</div>
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
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("avatar.secModel")}</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
            {[
              {k:"hunyuan-avatar",lKey:"modelHunyuan",dKey:"modelHunyuanDesc"},
              {k:"pixverse-lipsync",lKey:"modelPixverse",dKey:"modelPixverseDesc"}
            ].map(m=>(
              <button key={m.k} onClick={()=>setModel(m.k)}
                style={{textAlign:"left",padding:"0.7rem 0.9rem",border:model===m.k?"2px solid #0d0d0d":"1px solid #e5e5e5",background:model===m.k?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{t(`avatar.${m.lKey}`)}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{t(`avatar.${m.dKey}`)}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("avatar.secImage")}</div>
          <input ref={imgRef} type="file" accept="image/*" onChange={e=>setImage(e.target.files?.[0]||null)} style={{display:"none"}}/>
          <button onClick={()=>imgRef.current?.click()} style={{width:"100%",padding:"0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#666",fontSize:"0.85rem"}}>
            {image?`✓ ${image.name}`:t("avatar.clickUploadImg")}
          </button>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("avatar.secAudio")}</div>
          <input ref={audRef} type="file" accept="audio/*" onChange={e=>setAudio(e.target.files?.[0]||null)} style={{display:"none"}}/>
          <button onClick={()=>audRef.current?.click()} style={{width:"100%",padding:"0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#666",fontSize:"0.85rem"}}>
            {audio?`✓ ${audio.name}`:t("avatar.clickUploadAudio")}
          </button>
        </div>

        <div style={{flex:1}}/>

        {error && <div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}

        <button onClick={generate} disabled={loading}
          style={{padding:"0.9rem",background:loading?"#999":"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {loading?t("avatar.generatingBtn"):t("avatar.generate")}
        </button>
      </aside>
    </div>
  );
}
