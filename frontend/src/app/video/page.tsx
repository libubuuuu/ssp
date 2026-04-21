"use client";
import React, { useState, useEffect, useRef } from "react";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";
const MODES = [
  { key:"image-to-video", label:"图生视频", desc:"上传首帧，AI 生成动态视频" },
];

function VideoInput({url,setUrl,file,setFile,label}:{url:string,setUrl:(v:string)=>void,file:File|null,setFile:(f:File|null)=>void,label:string}){
  const vRef=React.useRef<HTMLInputElement>(null);
  const [tab,setTab]=React.useState<"url"|"upload">("url");
  return (
    <div>
      <div style={{display:"flex",gap:"0.3rem",marginBottom:"0.5rem"}}>
        <button onClick={()=>setTab("url")} style={{flex:1,padding:"0.35rem",fontSize:"0.75rem",border:tab==="url"?"2px solid #0d0d0d":"1px solid #e5e5e5",background:tab==="url"?"#f9f7f2":"#fff",borderRadius:"8px",cursor:"pointer",fontWeight:tab==="url"?600:400}}>链接</button>
        <button onClick={()=>setTab("upload")} style={{flex:1,padding:"0.35rem",fontSize:"0.75rem",border:tab==="upload"?"2px solid #0d0d0d":"1px solid #e5e5e5",background:tab==="upload"?"#f9f7f2":"#fff",borderRadius:"8px",cursor:"pointer",fontWeight:tab==="upload"?600:400}}>本地上传</button>
      </div>
      {tab==="url"?(
        <input value={url} onChange={e=>setUrl(e.target.value)} placeholder="粘贴视频 URL..." style={{width:"100%",padding:"0.65rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.85rem",background:"#fff",color:"#333",boxSizing:"border-box"}}/>
      ):(
        <>
          <input ref={vRef} type="file" accept="video/*" onChange={e=>{const f=e.target.files?.[0];if(f)setFile(f);}} style={{display:"none"}}/>
          <button onClick={()=>vRef.current?.click()} style={{width:"100%",padding:"1rem 0.9rem",border:file?"2px solid #0d0d0d":"2px dashed #ccc",background:file?"#f9f7f2":"#fafaf7",borderRadius:"12px",cursor:"pointer",color:file?"#0d0d0d":"#888",fontSize:"0.85rem",display:"flex",flexDirection:"column",alignItems:"center",gap:"0.4rem"}}>
            {file?(<><span style={{fontSize:"1.2rem"}}>OK</span><span style={{wordBreak:"break-all",textAlign:"center"}}>{file.name}</span><span style={{fontSize:"0.72rem",color:"#999"}}>点击更换</span></>):(<><span style={{fontSize:"1.4rem",color:"#bbb"}}>^</span><span>{label}</span></>)}
          </button>
        </>
      )}
    </div>
  );
}

export default function VideoPage(){
  const [mode,setMode]=useState("image-to-video");
  const [voiceMode,setVoiceMode]=useState<"silent"|"voiced">("silent");
  const [imageFile,setImageFile]=useState<File|null>(null);
  const [imagePreview,setImagePreview]=useState("");
  const [prompt,setPrompt]=useState("");
  const [duration,setDuration]=useState(5);
  const [srcVideoUrl,setSrcVideoUrl]=useState("");
  const [srcVideoFile,setSrcVideoFile]=useState<File|null>(null);
  const [elemFile,setElemFile]=useState<File|null>(null);
  const [elemPreview,setElemPreview]=useState("");
  const [instruction,setInstruction]=useState("");
  const [refVideoUrl,setRefVideoUrl]=useState("");
  const [refVideoFile,setRefVideoFile]=useState<File|null>(null);
  const [modelFile,setModelFile]=useState<File|null>(null);
  const [modelPreview,setModelPreview]=useState("");
  const [productFile,setProductFile]=useState<File|null>(null);
  const [productPreview,setProductPreview]=useState("");
  const [loading,setLoading]=useState(false);
  const [statusMsg,setStatusMsg]=useState("");
  const [error,setError]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);
  const fileRef=useRef<HTMLInputElement>(null);
  const pollRef=useRef<ReturnType<typeof setTimeout>|null>(null);

  useEffect(()=>{const saved=localStorage.getItem("video_gallery");if(saved){try{setGallery(JSON.parse(saved));}catch{}}return ()=>{ if(pollRef.current) clearTimeout(pollRef.current); };},[]); 
  const saveGallery=(g:any[])=>{setGallery(g);localStorage.setItem("video_gallery",JSON.stringify(g.slice(0,50)));};
  const handleImageFile=(f:File)=>{setImageFile(f);const r=new FileReader();r.onload=e=>setImagePreview(e.target?.result as string);r.readAsDataURL(f);};
  const handleElemFile=(f:File)=>{setElemFile(f);const r=new FileReader();r.onload=e=>setElemPreview(e.target?.result as string);r.readAsDataURL(f);};
  const handleModelFile=(f:File)=>{setModelFile(f);const r=new FileReader();r.onload=e=>setModelPreview(e.target?.result as string);r.readAsDataURL(f);};
  const handleProductFile=(f:File)=>{setProductFile(f);const r=new FileReader();r.onload=e=>setProductPreview(e.target?.result as string);r.readAsDataURL(f);};
  const uploadFile=async(f:File)=>{const token=localStorage.getItem("token")||"";const fd=new FormData();fd.append("file",f);const res=await fetch(`${API_BASE}/api/content/upload`,{method:"POST",headers:{"Authorization":`Bearer ${token}`},body:fd});const data=await res.json();if(!res.ok)throw new Error(data.detail||"上传失败");return data.url||data.image_url;};
  const pollStatus=async(taskId:string,endpointTag:string,attempt=0,currentGallery:any[],label:string)=>{if(attempt>72){setError("生成超时，请重试");setLoading(false);return;}try{const token=localStorage.getItem("token")||"";const res=await fetch(`${API_BASE}/api/tasks/status/${taskId}?endpoint=${endpointTag}`,{headers:{"Authorization":`Bearer ${token}`}});const data=await res.json();if(data.status==="completed"&&data.result_url){setStatusMsg("");setLoading(false);const newGallery=[{url:data.result_url,prompt:label,time:Date.now()},...currentGallery];saveGallery(newGallery);return;}if(data.status==="failed"){setError("生成失败，积分已返还");setLoading(false);return;}const mins=Math.floor(attempt*5/60);const secs=(attempt*5)%60;setStatusMsg(`AI 生成中，已等待 ${mins}分${secs}秒...`);pollRef.current=setTimeout(()=>pollStatus(taskId,endpointTag,attempt+1,currentGallery,label),5000);}catch{pollRef.current=setTimeout(()=>pollStatus(taskId,endpointTag,attempt+1,currentGallery,label),5000);}};
  
  const generateI2V=async()=>{if(!imageFile){setError("请上传首帧图片");return;}setError("");setLoading(true);setStatusMsg("正在上传图片...");try{const token=localStorage.getItem("token")||"";let imgUrl="";try{imgUrl=await uploadFile(imageFile);}catch{imgUrl=imagePreview;}setStatusMsg("正在提交生成任务...");const endpoint="/api/video/image-to-video";const res=await fetch(`${API_BASE}${endpoint}`,{method:"POST",headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},body:JSON.stringify({image_url:imgUrl,prompt,duration_sec:duration}),});const data=await res.json();if(!res.ok)throw new Error(data.detail||"提交失败");if(!data.task_id)throw new Error("未获取到任务ID");const label=voiceMode==="silent"?"图生视频(无声)":"图生视频(有声)";setStatusMsg("任务已提交，AI 生成中（约 2-5 分钟）...");pollRef.current=setTimeout(()=>pollStatus(data.task_id,data.endpoint_tag||"i2v",0,gallery,label),3000);}catch(e:any){setError(e.message);setLoading(false);setStatusMsg("");}};
  
  const generateReplace=async()=>{if(!srcVideoUrl&&!srcVideoFile){setError("请输入原视频链接或上传视频");return;}if(!elemFile){setError("请上传替换元素图片");return;}if(!instruction){setError("请输入替换指令");return;}setError("");setLoading(true);setStatusMsg("正在上传图片...");try{const token=localStorage.getItem("token")||"";let elemUrl="";try{elemUrl=await uploadFile(elemFile);}catch{elemUrl=elemPreview;}setStatusMsg("正在提交替换任务...");const res=await fetch(`${API_BASE}/api/video/replace/element`,{method:"POST",headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},body:JSON.stringify({video_url:srcVideoUrl,element_image_url:elemUrl,instruction}),});const data=await res.json();if(!res.ok)throw new Error(data.detail||"提交失败");if(!data.task_id)throw new Error("未获取到任务ID");setStatusMsg("任务已提交，AI 替换中（约 2-5 分钟）...");pollRef.current=setTimeout(()=>pollStatus(data.task_id,data.endpoint_tag||"video_edit",0,gallery,instruction),3000);}catch(e:any){setError(e.message);setLoading(false);setStatusMsg("");}};
  
  const generateRemake=async()=>{if(!refVideoUrl&&!refVideoFile){setError("请输入参考视频链接或上传视频");return;}if(!modelFile){setError("请上传模特图片");return;}setError("");setLoading(true);setStatusMsg("正在上传图片...");try{const token=localStorage.getItem("token")||"";let modelUrl="";let productUrl="";let videoUrl=refVideoUrl;try{modelUrl=await uploadFile(modelFile);}catch{modelUrl=modelPreview;}if(productFile){try{productUrl=await uploadFile(productFile);}catch{productUrl=productPreview;}}if(refVideoFile){setStatusMsg("正在上传参考视频...");const fd=new FormData();fd.append("file",refVideoFile);const upRes=await fetch(`${API_BASE}/api/video/upload/video`,{method:"POST",headers:{"Authorization":`Bearer ${token}`},body:fd});const upData=await upRes.json();if(!upRes.ok)throw new Error(upData.detail||"视频上传失败");videoUrl=upData.url||upData.video_url;}if(!videoUrl||!/^https?:/.test(videoUrl))throw new Error("参考视频必须是 HTTPS URL 或上传文件");setStatusMsg("正在提交翻拍任务...");const res=await fetch(`${API_BASE}/api/video/clone`,{method:"POST",headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},body:JSON.stringify({reference_video_url:videoUrl,model_image_url:modelUrl,product_image_url:productUrl||undefined}),});const data=await res.json();if(!res.ok)throw new Error(data.detail||"提交失败");if(!data.task_id)throw new Error("未获取到任务ID");setStatusMsg("任务已提交，AI 翻拍中（约 3-5 分钟）...");pollRef.current=setTimeout(()=>pollStatus(data.task_id,data.endpoint_tag||"video_clone",0,gallery,"翻拍复刻"),3000);}catch(e:any){setError(e.message);setLoading(false);setStatusMsg("");}};
  
  const handleGenerate=()=>{if(mode==="image-to-video")generateI2V();else if(mode==="element-replace")generateReplace();else if(mode==="remake")generateRemake();};
  
  const UploadBtn=({preview,onClick,label}:{preview:string,onClick:()=>void,label:string})=>(preview?<div onClick={onClick} style={{position:"relative",cursor:"pointer",borderRadius:"12px",overflow:"hidden",background:"#fafaf7"}}><img src={preview} alt="" style={{width:"100%",objectFit:"cover"}}/><div style={{position:"absolute",inset:0,background:"rgba(0,0,0,0.5)",display:"flex",alignItems:"center",justifyContent:"center",color:"#fff",fontSize:"0.85rem",opacity:0,transition:"opacity 0.2s"}} onMouseEnter={e=>e.currentTarget.style.opacity="1"} onMouseLeave={e=>e.currentTarget.style.opacity="0"}>点击更换</div></div>:<button onClick={onClick} style={{width:"100%",padding:"1.2rem 0.9rem",border:"2px dashed #ccc",background:"#fafaf7",borderRadius:"12px",cursor:"pointer",color:"#888",fontSize:"0.85rem",display:"flex",flexDirection:"column",alignItems:"center",gap:"0.4rem"}}><span style={{fontSize:"1.4rem",color:"#bbb"}}>↑</span>{label}</button>);

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
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>
                {mode==="image-to-video"&&`在右侧上传首帧图片${voiceMode==="voiced"?"(有声)":"(无声)"}，点击「开始生成」`}
                {mode==="element-replace"&&"在右侧输入原视频链接和替换元素，点击「开始替换」"}
                {mode==="remake"&&"在右侧上传参考视频链接和模特图，点击「开始翻拍」"}
              </div>
            </div>
          )}
          {loading&&(
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#555",fontSize:"0.95rem",fontWeight:500}}>{statusMsg||"AI 正在生成视频..."}</div>
              <div style={{marginTop:"0.5rem",color:"#bbb",fontSize:"0.78rem"}}>请不要关闭此页面，视频生成需要 2-5 分钟</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0&&!loading&&(
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",boxShadow:"0 4px 12px rgba(0,0,0,0.04)"}}>
                  <video src={item.url} controls style={{width:"100%"}}/>
                  <div style={{padding:"0.5rem 0.75rem",fontSize:"0.75rem",color:"#888"}}>{(item.prompt||"").slice(0,40)}</div>
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

        {mode==="image-to-video"&&(<>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>音声模式</div>
            <div style={{display:"flex",gap:"0.5rem"}}>
              <label style={{flex:1,display:"flex",alignItems:"center",gap:"0.4rem",padding:"0.6rem",border:voiceMode==="silent"?"2px solid #0d0d0d":"1px solid #e5e5e5",background:voiceMode==="silent"?"#f9f7f2":"#fff",borderRadius:"8px",cursor:"pointer",fontSize:"0.85rem"}}>
                <input type="radio" checked={voiceMode==="silent"} onChange={()=>setVoiceMode("silent")} style={{cursor:"pointer"}}/>
                无声
              </label>
              <label style={{flex:1,display:"flex",alignItems:"center",gap:"0.4rem",padding:"0.6rem",border:voiceMode==="voiced"?"2px solid #0d0d0d":"1px solid #e5e5e5",background:voiceMode==="voiced"?"#f9f7f2":"#fff",borderRadius:"8px",cursor:"pointer",fontSize:"0.85rem"}}>
                <input type="radio" checked={voiceMode==="voiced"} onChange={()=>setVoiceMode("voiced")} style={{cursor:"pointer"}}/>
                有声
              </label>
            </div>
          </div>
          
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>首帧 / 尾帧</div>
            <input ref={fileRef} type="file" accept="image/*" onChange={e=>{const f=e.target.files?.[0];if(f)handleImageFile(f);}} style={{display:"none"}}/>
            <UploadBtn preview={imagePreview} onClick={()=>fileRef.current?.click()} label="点击上传首帧图片"/>
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
        </>)}

        {mode==="element-replace"&&(<>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>原视频</div>
            <VideoInput url={srcVideoUrl} setUrl={setSrcVideoUrl} file={srcVideoFile} setFile={setSrcVideoFile} label="点击上传原视频"/>
          </div>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>新元素图片</div>
            <input type="file" accept="image/*" id="elemInput" onChange={e=>{const f=e.target.files?.[0];if(f)handleElemFile(f);}} style={{display:"none"}}/>
            <UploadBtn preview={elemPreview} onClick={()=>document.getElementById("elemInput")?.click()} label="上传替换元素图片"/>
          </div>
          <div style={{flex:1,display:"flex",flexDirection:"column"}}>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>替换指令</div>
            <textarea value={instruction} onChange={e=>setInstruction(e.target.value)} placeholder="例如：把视频里的水杯替换成我的产品" style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"80px",resize:"vertical",fontFamily:"inherit",background:"#fff",color:"#333",flex:1}}/>
          </div>
        </>)}

        {mode==="remake"&&(<>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>参考视频</div>
            <VideoInput url={refVideoUrl} setUrl={setRefVideoUrl} file={refVideoFile} setFile={setRefVideoFile} label="点击上传参考视频"/>
            <div style={{fontSize:"0.72rem",color:"#bbb",marginTop:"0.4rem"}}>系统将提取该视频的运镜节奏用于翻拍</div>
          </div>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>我的模特图 *</div>
            <input type="file" accept="image/*" id="modelInput" onChange={e=>{const f=e.target.files?.[0];if(f)handleModelFile(f);}} style={{display:"none"}}/>
            <UploadBtn preview={modelPreview} onClick={()=>document.getElementById("modelInput")?.click()} label="上传模特图片"/>
          </div>
          <div>
            <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>我的产品图（可选）</div>
            <input type="file" accept="image/*" id="productInput" onChange={e=>{const f=e.target.files?.[0];if(f)handleProductFile(f);}} style={{display:"none"}}/>
            <UploadBtn preview={productPreview} onClick={()=>document.getElementById("productInput")?.click()} label="上传产品图片（可选）"/>
          </div>
        </>)}

        {error&&<div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}
        <button onClick={handleGenerate} disabled={loading} style={{padding:"0.9rem",background:loading?"#999":"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {loading?"生成中...":mode==="image-to-video"?`开始生成${voiceMode==="voiced"?"(有声)":"(无声)"}`:mode==="element-replace"?"开始替换":"开始翻拍"}
        </button>
      </aside>
    </div>
  );
}
