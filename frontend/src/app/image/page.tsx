"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { GalleryItem } from "@/lib/types/gallery";
import { errMsg } from "@/lib/utils/errors";
import { compressImage } from "@/lib/utils/imageCompress";
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const STYLES = [
  { key:"advertising", labelKey:"advertising" },
  { key:"minimalist", labelKey:"minimalist" },
  { key:"custom", labelKey:"custom" },
];
export default function ImagePage(){
  const { t } = useLang();
  const [prompt,setPrompt]=useState("");
  const [style,setStyle]=useState("advertising");
  const [model, setModel] = useState("gpt-image-2");
  const MODEL_OPTIONS = [
    { key: "gpt-image-2", label: "标准模式", cost: "20 积分/张" },
    { key: "aiview-pro",  label: "专业版",   cost: "25 积分/张" },
  ];
  const [size,setSize]=useState("1:1");
  const [refImages,setRefImages]=useState<string[]>([]);
  const [refPreviews,setRefPreviews]=useState<string[]>([]);
  const [uploading,setUploading]=useState(false);
  // 并行生成:每个进行中的任务一项,提交后不阻塞、可继续提交,完成后陆续填进网格。
  const [pending,setPending]=useState<{id:string;prompt:string;size:string;startedAt:number}[]>([]);
  const [submitting,setSubmitting]=useState(false);  // 仅"提交请求"那一瞬,防重复点
  const [nowTs,setNowTs]=useState(()=>Date.now());   // 每秒 +1,用于算各任务已用时
  const MAX_PARALLEL=6;                              // 客户端软上限(后端 Semaphore 也会兜底排队)
  const [error,setError]=useState("");
  const [msg,setMsg]=useState("");
  const [gallery,setGallery]=useState<GalleryItem[]>([]);
  useEffect(()=>{
    const userData=localStorage.getItem("user")||"{}";
    let userId="anonymous";
    try{userId=JSON.parse(userData).id||"anonymous";}catch{}
    const saved=localStorage.getItem(`img_gallery_${userId}`);
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
  },[]);
  const getUid=()=>{try{return JSON.parse(localStorage.getItem("user")||"{}").id||"anonymous";}catch{return "anonymous";}};
  // race-safe:并行任务可能同时完成,用函数式更新基于最新 state 前插,避免互相覆盖丢图。
  const addToGallery=(item:GalleryItem)=>{
    setGallery(prev=>{
      const next=[item,...prev].slice(0,50);
      localStorage.setItem(`img_gallery_${getUid()}`,JSON.stringify(next));
      return next;
    });
  };
  // 进行中任务的"已用时"每秒刷新(仅在有任务时跑)。
  useEffect(()=>{
    if(pending.length===0)return;
    const id=setInterval(()=>setNowTs(Date.now()),1000);
    return ()=>clearInterval(id);
  },[pending.length]);
  const handleRefUpload=async(e:React.ChangeEvent<HTMLInputElement>)=>{
    const file=e.target.files?.[0];
    if(!file)return;
    if(refImages.length>=5){setError(t("errors.maxRefImages"));return;}
    setError("");setUploading(true);
    try{
      // 七十三续:前端压缩,5MB → 500KB,上传 30s → 3s
      setMsg(t("image.compressing"));
      const compressed = await compressImage(file);
      setMsg("");
      const token=localStorage.getItem("token")||"";
      const fd=new FormData();
      fd.append("file",compressed);
      const res=await fetch(`${API_BASE}/api/image/upload/cos`,{
        method:"POST",
        headers:{"Authorization":`Bearer ${token}`},
        body:fd,
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||t("errors.uploadFailed"));
      const preview=URL.createObjectURL(file);
      setRefImages([...refImages,data.url]);
      setRefPreviews([...refPreviews,preview]);
    }catch(err){setError(errMsg(err));}
    finally{setUploading(false);e.target.value="";}
  };
  const removeRef=(i:number)=>{
    setRefImages(refImages.filter((_,idx)=>idx!==i));
    setRefPreviews(refPreviews.filter((_,idx)=>idx!==i));
  };
  const generate=async()=>{
    if(!prompt.trim()){setError(t("errors.inputPrompt"));return;}
    if(pending.length>=MAX_PARALLEL){setError(`最多同时生成 ${MAX_PARALLEL} 张，请等部分完成再继续`);return;}
    setError("");setSubmitting(true);
    const jobPrompt=prompt, jobSize=size;   // 锁定本次提交的 prompt/size(用户随后可改)
    try{
      const token=localStorage.getItem("token")||"";
      const res=await fetch(`${API_BASE}/api/jobs/submit`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({
          type:"image",
          title:jobPrompt.slice(0,30),
          params:{
            prompt:jobPrompt,
            reference_images:refImages,
            size:jobSize,
            model,
            style,
            aspect_ratio:jobSize,
          },
        }),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||t("errors.submitFailed"));
      if(typeof data.cost==="number"&&data.cost>0)adjustLocalUserCredits(-data.cost);
      // 入 pending(不阻塞,用户可立刻再点生成),后台独立监听该 job 完成。
      setPending(p=>[...p,{id:data.job_id,prompt:jobPrompt,size:jobSize,startedAt:Date.now()}]);
      setMsg("已提交，可继续生成下一张");
      setTimeout(()=>setMsg(""),2500);
      watchJob(data.job_id,jobPrompt);
    }catch(e){
      setError(errMsg(e));
    }finally{
      setSubmitting(false);
    }
  };
  const finishJob=(jobId:string)=>setPending(p=>p.filter(x=>x.id!==jobId));

  const onJobFailed=(raw:string,jobId:string)=>{
    if(raw.includes("content_policy_violation")||raw.includes("安全审核")){
      setError("某张生成被安全审核拦截，请修改描述后重试（建议：减少人物动作描述，或改用英文）");
    }else{
      setError(raw.slice(0,120)||"某张生图失败，请重试");
    }
    finishJob(jobId);
  };
  const pollJob=async(jobId:string,jobPrompt:string)=>{
    const token=localStorage.getItem("token")||"";
    let sec=0;
    while(sec<960){
      await new Promise(r=>setTimeout(r,3000));
      sec+=3;
      try{
        const res=await fetch(`${API_BASE}/api/jobs/${jobId}`,{
          headers:{"Authorization":`Bearer ${token}`},
        });
        const j=await res.json();
        if(j.status==="completed"&&j.result?.image_url){
          addToGallery({url:j.result.image_url,prompt:jobPrompt,time:Date.now()});
          finishJob(jobId);return;
        }
        if(j.status==="failed"){onJobFailed(j.error||"",jobId);return;}
      }catch{}
    }
    setError("某张生成超时（>15分钟），请重试");
    finishJob(jobId);
  };
  // 单个 job 独立监听:SSE 完成即推送,失败降级轮询。完成/失败都把它从 pending 摘掉。
  const watchJob=(jobId:string,jobPrompt:string)=>{
    const sse=new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`,{withCredentials:true});
    const cleanup=()=>sse.close();
    sse.onmessage=(e)=>{
      cleanup();
      try{
        const d=JSON.parse(e.data);
        if(d.status==="completed"&&d.result?.image_url){
          addToGallery({url:d.result.image_url,prompt:jobPrompt,time:Date.now()});
          finishJob(jobId);
        }else if(d.status==="failed"){
          onJobFailed(d.error||"",jobId);
        }else{
          finishJob(jobId);
        }
      }catch{finishJob(jobId);}
    };
    sse.onerror=()=>{
      // SSE 建立失败(如 token 过期)→ 降级轮询
      cleanup();
      pollJob(jobId,jobPrompt);
    };
  };
  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto",maxWidth:"1280px",width:"100%",margin:"0 auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>{t("dashboard.features.image.desc")}</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{t("dashboard.features.image.label")}</h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm(t("confirms.clearCanvas"))){setGallery([]);localStorage.setItem(`img_gallery_${getUid()}`,"[]");}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>{t("image.clearBtn")}</button>}
        </div>
        <div style={{marginBottom:"1rem",padding:"0.6rem 1rem",background:"#fffbeb",border:"1px solid #fde68a",borderRadius:"8px",fontSize:"0.82rem",color:"#92400e"}}>
          生成的图片保存 <b>7 天</b>，到期后自动清除（积分记录不受影响）。请及时下载保存。
        </div>
        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && pending.length===0 && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>◧</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>{t("image.empty")}</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>{t("image.emptyTip")}</div>
            </div>
          )}
          {(gallery.length>0 || pending.length>0) && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"1rem"}}>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
              {/* 进行中任务:占位块(并行,各自独立计时),完成后自动从这里消失、出现在下方网格 */}
              {pending.map((p)=>(
                <div key={p.id} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:p.size.replace(":","/"),boxShadow:"0 4px 12px rgba(0,0,0,0.04)",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",border:"1px solid #eee"}}>
                  <div style={{width:"34px",height:"34px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
                  <div style={{marginTop:"0.7rem",color:"#888",fontSize:"0.82rem"}}>{t("image.creating")} {Math.max(0,Math.floor((nowTs-p.startedAt)/1000))}s</div>
                  <div style={{position:"absolute",bottom:0,left:0,right:0,padding:"0.6rem",fontSize:"0.72rem",color:"#aaa",textAlign:"center",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{(p.prompt||"").slice(0,30)}</div>
                </div>
              ))}
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:size.replace(":","/"),boxShadow:"0 4px 12px rgba(0,0,0,0.04)",cursor:"pointer"}}
                  onClick={async()=>{
                    try{
                      const res=await fetch(item.url);
                      const blob=await res.blob();
                      const a=document.createElement("a");
                      a.href=URL.createObjectURL(blob);
                      a.download=`image_${item.time||Date.now()}.png`;
                      a.click();
                      URL.revokeObjectURL(a.href);
                    }catch{window.open(item.url,"_blank");}
                  }}>
                  <img src={item.url} alt="" style={{width:"100%",height:"100%",objectFit:"cover"}}/>
                  <div style={{position:"absolute",top:"0.5rem",right:"0.5rem",background:"rgba(0,0,0,0.6)",color:"#fff",padding:"0.3rem 0.6rem",borderRadius:"999px",fontSize:"0.7rem"}}>⬇ {t("common.download")}</div>
                  <div style={{position:"absolute",bottom:0,left:0,right:0,padding:"0.75rem",background:"linear-gradient(transparent,rgba(0,0,0,0.75))",color:"#fff",fontSize:"0.75rem"}}>{(item.prompt||"").slice(0,40)}{(item.prompt||"").length>40?"...":""}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
      <aside style={{width:"340px",background:"#fff",borderLeft:"1px solid rgba(0,0,0,0.06)",padding:"2rem 1.75rem",display:"flex",flexDirection:"column",gap:"1.25rem",height:"100vh",position:"sticky",top:0,overflowY:"auto"}}>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>模型</div>
          <div style={{display:"flex",gap:"0.4rem"}}>
            {MODEL_OPTIONS.map(o=>(
              <button key={o.key} onClick={()=>setModel(o.key)}
                style={{flex:1,padding:"0.55rem 0.5rem",border:model===o.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:model===o.key?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer",fontSize:"0.82rem",color:"#333",textAlign:"center"}}>
                <div style={{fontWeight:500}}>{o.label}</div>
                <div style={{fontSize:"0.7rem",color:model===o.key?"#555":"#aaa",marginTop:"0.15rem"}}>{o.cost}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.reference")}</div>
          <div style={{display:"flex",gap:"0.4rem",flexWrap:"wrap"}}>
            {refPreviews.map((p,i)=>(
              <div key={i} style={{position:"relative",width:60,height:60}}>
                <img src={p} alt={t("image.refAlt")} style={{width:"100%",height:"100%",objectFit:"cover",borderRadius:8}}/>
                <button onClick={()=>removeRef(i)} style={{position:"absolute",top:-6,right:-6,width:18,height:18,borderRadius:"50%",background:"#c00",color:"#fff",border:"none",cursor:"pointer",fontSize:"0.7rem",lineHeight:1}}>×</button>
              </div>
            ))}
            {refImages.length<5 && (
              <label style={{width:60,height:60,border:"2px dashed #ccc",borderRadius:8,cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",color:"#999",fontSize:"1.2rem",background:"#fafaf7"}}>
                <input type="file" accept="image/*" style={{display:"none"}} onChange={handleRefUpload}/>
                {uploading?"…":"+"}
              </label>
            )}
          </div>
          {refImages.length>0 && <div style={{fontSize:"0.7rem",color:"#888",marginTop:"0.4rem"}}>{t("image.addedRefs")} {refImages.length} {t("image.images")}</div>}
        </div>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.style")}</div>
          <div style={{display:"flex",gap:"0.4rem",flexWrap:"wrap"}}>
            {STYLES.map(s=>(
              <button key={s.key} onClick={()=>setStyle(s.key)}
                style={{padding:"0.45rem 0.9rem",border:style===s.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:style===s.key?"#f9f7f2":"#fff",borderRadius:"999px",cursor:"pointer",fontSize:"0.8rem",color:"#333"}}>
                {t(`image.styles.${s.labelKey}`)}
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.size")}</div>
          <select value={size} onChange={e=>setSize(e.target.value)} style={{width:"100%",padding:"0.65rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.85rem",background:"#fff",color:"#333"}}>
            <option value="1:1">正方形 1:1</option>
            <option value="9:16">竖版 9:16</option>
            <option value="3:4">竖版 3:4</option>
            <option value="16:9">横版 16:9</option>
          </select>
        </div>
        <div style={{flex:1,display:"flex",flexDirection:"column"}}>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.prompt")}</div>
          <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder={refImages.length>0?t("image.promptWithRef"):t("image.promptPlaceholder")}
            style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"120px",resize:"vertical",fontFamily:"inherit",background:"#fff",color:"#333",flex:1}}/>
        </div>
        {msg && <div style={{color:"#0a0",background:"#eaf7ea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{msg}</div>}
        {error && <div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}
<button onClick={generate} disabled={submitting}
          style={{padding:"0.9rem",background:"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:submitting?"wait":"pointer",fontSize:"0.95rem",fontWeight:500,opacity:submitting?0.6:1}}>
          {t("image.generate")}{pending.length>0?`（生成中 ${pending.length}）`:""}
        </button>
      </aside>
    </div>
  );
}
