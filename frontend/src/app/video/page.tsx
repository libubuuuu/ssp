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
  const [startFile,setStartFile]=useState<File|null>(null);
  const [startPreview,setStartPreview]=useState("");
  const [endFile,setEndFile]=useState<File|null>(null);
  const [endPreview,setEndPreview]=useState("");
  const [prompt,setPrompt]=useState("");
  const [duration,setDuration]=useState(5);
  const [loading,setLoading]=useState(false);
  const [statusMsg,setStatusMsg]=useState("");
  const [error,setError]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);
  const startRef=useRef<HTMLInputElement>(null);
  const endRef=useRef<HTMLInputElement>(null);
  const pollRef=useRef<ReturnType<typeof setTimeout>|null>(null);

  useEffect(()=>{
    const saved=localStorage.getItem("video_gallery");
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
    return ()=>{ if(pollRef.current) clearTimeout(pollRef.current); };
  },[]);

  const saveGallery=(g:any[])=>{
    setGallery(g);
    localStorage.setItem("video_gallery",JSON.stringify(g.slice(0,50)));
  };

  const handleFile=(f:File,type:"start"|"end")=>{
    const reader=new FileReader();
    reader.onload=e=>{
      if(type==="start"){setStartFile(f);setStartPreview(e.target?.result as string);}
      else{setEndFile(f);setEndPreview(e.target?.result as string);}
    };
    reader.readAsDataURL(f);
  };

  const uploadFile=async(f:File)=>{
    const token=localStorage.getItem("token")||"";
    const fd=new FormData();
    fd.append("file",f);
    const res=await fetch(`${API_BASE}/api/content/upload`,{method:"POST",headers:{"Authorization":`Bearer ${token}`},body:fd});
    const data=await res.json();
    if(!res.ok)throw new Error(data.detail||"上传失败");
    return data.url||data.image_url;
  };

  const pollStatus=async(taskId:string,endpointTag:string,attempt=0,currentGallery:any[])=>{
    if(attempt>72){setError("生成超时，请重试");setLoading(false);return;}
    try{
      const token=localStorage.getItem("token")||"";
      const res=await fetch(`${API_BASE}/api/tasks/status/${taskId}?endpoint=${endpointTag}&prompt=${encodeURIComponent(prompt||"图生视频")}`,{headers:{"Authorization":`Bearer ${token}`}});
      const data=await res.json();
      if(data.status==="completed"&&data.result_url){
        setStatusMsg("");setLoading(false);
        const newGallery=[{url:data.result_url,prompt:prompt||"图生视频",time:Date.now()},...currentGallery];
        saveGallery(newGallery);
        return;
      }
      if(data.status==="failed"){setError("生成失败，积分已返还");setLoading(false);return;}
      const mins=Math.floor(attempt*5/60);
      const secs=(attempt*5)%60;
      setStatusMsg(`AI 生成中，已等待 ${mins}分${secs}秒...`);
      pollRef.current=setTimeout(()=>pollStatus(taskId,endpointTag,attempt+1,currentGallery),5000);
    }catch{
      pollRef.current=setTimeout(()=>pollStatus(taskId,endpointTag,attempt+1,currentGallery),5000);
    }
  };

  const generate=async()=>{
    if(!startFile){setError("请上传首帧图片");return;}
    setError("");setLoading(true);setStatusMsg("正在上传图片...");
    try{
      const token=localStorage.getItem("token")||"";
      let startUrl="";
      let endUrl="";
      try{startUrl=await uploadFile(startFile);}catch{startUrl=startPreview;}
      if(endFile){
        setStatusMsg("正在上传尾帧...");
        try{endUrl=await uploadFile(endFile);}catch{endUrl=endPreview;}
      }
      setStatusMsg("正在提交生成任务...");
      const body:any={image_url:startUrl,prompt,duration_sec:duration};
      if(endUrl) body.tail_image_url=endUrl;
      const res=await fetch(`${API_BASE}/api/video/image-to-video`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify(body),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"提交失败");
      if(!data.task_id)throw new Error("未获取到任务ID");
      setStatusMsg("任务已提交，AI 生成中（约 1 分钟）...");
      pollRef.current=setTimeout(()=>pollStatus(data.task_id,data.endpoint_tag||"i2v",0,gallery),3000);
    }catch(e:any){
      setError(e.message);setLoading(false);setStatusMsg("");
    }
  };

  const UploadBox=({preview,fileRef,label,onFile}:{preview:string,fileRef:any,label:string,onFile:(f:File)=>void})=>(
    <div style={{flex:1}}>
      <div style={{fontSize:"0.72rem",color:"#999",marginBottom:"0.4rem"}}>{label}</div>
      <input ref={fileRef} type="file" accept="image/*" onChange={e=>{const f=e.target.files?.[0];if(f)onFile(f);}} style={{display:"none"}}/>
      {preview?(
        <div onClick={()=>fileRef.current?.click()} style={{position:"relative",cursor:"pointer",borderRadius:"10px",overflow:"hidden",aspectRatio:"9/16",background:"#fafaf7"}}>
          <img src={preview} alt="" style={{width:"100%",height:"100%",objectFit:"cover"}}/>
          <div style={{position:"absolute",inset:0,background:"rgba(0,0,0,0.4)",display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontSize:"0.8rem",opacity:0,transition:"opacity 0.2s"}} onMouseEnter={e=>e.currentTarget.style.opacity="1"} onMouseLeave={e=>e.currentTarget.style.opacity="0"}>点击更换</div>
        </div>
      ):(
        <button onClick={()=>fileRef.current?.click()} style={{width:"100%",aspectRatio:"9/16",border:"2px dashed #ddd",background:"#fafaf7",borderRadius:"10px",cursor:"pointer",color:"#aaa",fontSize:"0.8rem",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",gap:"0.4rem"}}>
          <span style={{fontSize:"1.2rem"}}>↑</span>上传{label}
        </button>
      )}
    </div>
  );

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>视频创作</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>视频<span style={{fontStyle:"italic"}}> 画布</span></h1>
          </div>
          {gallery.length>0&&<button onClick={()=>{if(confirm("清空画布？")){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>
        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(0,0,0,0.05) 1px,transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0&&!loading&&(
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>▶</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>还没有视频作品，开始你的第一次创作吧</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>在右侧上传首帧图片，点击「开始生成」</div>
            </div>
          )}
          {loading&&(
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#555",fontSize:"0.95rem",fontWeight:500}}>{statusMsg||"AI 正在生成视频..."}</div>
              <div style={{marginTop:"0.5rem",color:"#bbb",fontSize:"0.78rem"}}>请不要关闭此页面，视频生成需要约 1 分钟</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0&&!loading&&(
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",boxShadow:"0 4px 12px rgba(0,0,0,0.04)"}}>
                  <video src={item.url} controls style={{width:"100%",display:"block"}}/>
                  <div style={{padding:"0.6rem 0.8rem",fontSize:"0.75rem",color:"#888"}}>{(item.prompt||"").slice(0,40)}</div>
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
              <button key={m.key} onClick={()=>setMode(m.key)} style={{textAlign:"left",padding:"0.7rem 0.9rem",border:mode===m.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:mode===m.key?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{m.label}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>首帧 / 尾帧</div>
          <div style={{display:"flex",gap:"0.6rem"}}>
            <UploadBox preview={startPreview} fileRef={startRef} label="首帧" onFile={f=>handleFile(f,"start")}/>
            <div style={{display:"flex",alignItems:"center",color:"#ccc",fontSize:"1.2rem",paddingTop:"1.5rem"}}>→</div>
            <UploadBox preview={endPreview} fileRef={endRef} label="尾帧（可选）" onFile={f=>handleFile(f,"end")}/>
          </div>
        </div>

        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>时长</div>
          <select value={duration} onChange={e=>setDuration(parseInt(e.target.value))} style={{width:"100%",padding:"0.65rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.85rem",background:"#fff",color:"#333"}}>
            <option value="5">5 秒</option>
            <option value="10">10 秒</option>
          </select>
        </div>

        <div style={{flex:1,display:"flex",flexDirection:"column"}}>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>运动描述（可选）</div>
          <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="描述视频中期望的运动效果..." style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"80px",resize:"vertical",fontFamily:"inherit",background:"#fff",color:"#333",flex:1}}/>
        </div>

        {error&&<div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}
        <button onClick={generate} disabled={loading} style={{padding:"0.9rem",background:loading?"#999":"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {loading?"生成中...":"开始生成"}
        </button>
      </aside>
    </div>
  );
}
