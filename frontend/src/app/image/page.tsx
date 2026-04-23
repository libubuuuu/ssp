"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import Sidebar from "@/components/Sidebar";
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const STYLES = [
  { key:"advertising", labelKey:"advertising" },
  { key:"minimalist", labelKey:"minimalist" },
  { key:"custom", labelKey:"custom" },
];
const MODELS = [
  { key:"nano-banana-2", labelKey:"economy", descKey:"economyDesc" },
  { key:"flux/schnell", labelKey:"fast", descKey:"fastDesc" },
  { key:"flux/dev", labelKey:"pro", descKey:"proDesc" },
];
export default function ImagePage(){
  const { t } = useLang();
  const [prompt,setPrompt]=useState("");
  const [style,setStyle]=useState("advertising");
  const [model,setModel]=useState("nano-banana-2");
  const [size,setSize]=useState("1024x1024");
  const [refImages,setRefImages]=useState<string[]>([]);
  const [refPreviews,setRefPreviews]=useState<string[]>([]);
  const [uploading,setUploading]=useState(false);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [msg,setMsg]=useState("");
  const [gallery,setGallery]=useState<any[]>([]);
  useEffect(()=>{
    const userData=localStorage.getItem("user")||"{}";
    let userId="anonymous";
    try{userId=JSON.parse(userData).id||"anonymous";}catch{}
    const saved=localStorage.getItem(`img_gallery_${userId}`);
    if(saved){try{setGallery(JSON.parse(saved));}catch{}}
  },[]);
  const saveGallery=(g:any[])=>{
    setGallery(g);
    const userData2=localStorage.getItem("user")||"{}";
    let userId2="anonymous";
    try{userId2=JSON.parse(userData2).id||"anonymous";}catch{}
    localStorage.setItem(`img_gallery_${userId2}`,JSON.stringify(g.slice(0,50)));
  };
  const handleRefUpload=async(e:React.ChangeEvent<HTMLInputElement>)=>{
    const file=e.target.files?.[0];
    if(!file)return;
    if(refImages.length>=5){setError("最多上传 5 张参考图");return;}
    setError("");setUploading(true);
    try{
      const token=localStorage.getItem("token")||"";
      const fd=new FormData();
      fd.append("file",file);
      const res=await fetch(`${API_BASE}/api/video/upload/image`,{
        method:"POST",
        headers:{"Authorization":`Bearer ${token}`},
        body:fd,
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"上传失败");
      const preview=URL.createObjectURL(file);
      setRefImages([...refImages,data.url]);
      setRefPreviews([...refPreviews,preview]);
    }catch(err:any){setError(err.message);}
    finally{setUploading(false);e.target.value="";}
  };
  const removeRef=(i:number)=>{
    setRefImages(refImages.filter((_,idx)=>idx!==i));
    setRefPreviews(refPreviews.filter((_,idx)=>idx!==i));
  };
  const generate=async()=>{
    if(!prompt.trim()){setError("请输入提示词");return;}
    setError("");
    try{
      const token=localStorage.getItem("token")||"";
      // 投递到全局任务队列
      const res=await fetch(`${API_BASE}/api/jobs/submit`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({
          type:"image",
          title:prompt.slice(0,30),
          params:{
            prompt,
            reference_images:refImages,
            size,
            model,
            style,
          },
        }),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||"提交失败");
      // 立刻返回，不阻塞。任务完成后右下角浮窗会显示
      setMsg(`任务已提交！查看右下角⚡ 我的任务`);
      setTimeout(()=>setMsg(""),3000);
      // 后台轮询这个任务，完成后加入 gallery
      pollJob(data.job_id,prompt);
    }catch(e:any){setError(e.message);}
  };

  const pollJob=async(jobId:string,jobPrompt:string)=>{
    const token=localStorage.getItem("token")||"";
    const start=Date.now();
    while(Date.now()-start<300000){ // 最多 5 分钟
      await new Promise(r=>setTimeout(r,3000));
      try{
        const res=await fetch(`${API_BASE}/api/jobs/${jobId}`,{
          headers:{"Authorization":`Bearer ${token}`},
        });
        const j=await res.json();
        if(j.status==="completed"&&j.result?.image_url){
          const ud=localStorage.getItem("user")||"{}";let uid="anonymous";try{uid=JSON.parse(ud).id||"anonymous";}catch{}
          saveGallery([{url:j.result.image_url,prompt:jobPrompt,time:Date.now()},...JSON.parse(localStorage.getItem(`img_gallery_${uid}`)||"[]")]);
          return;
        }
        if(j.status==="failed")return;
      }catch{}
    }
  };
  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"2rem 2.5rem",overflowY:"auto"}}>
        <div style={{marginBottom:"1.5rem",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div>
            <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>{t("image.title")}</div>
            <h1 style={{fontSize:"1.6rem",fontWeight:400,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{t("image.myCanvas")}<span style={{fontStyle:"italic"}}> {t("image.canvas")}</span></h1>
          </div>
          {gallery.length>0 && <button onClick={()=>{if(confirm("清空画布？")){saveGallery([]);}}} style={{background:"none",border:"1px solid #ddd",padding:"0.5rem 1rem",borderRadius:"999px",color:"#666",fontSize:"0.85rem",cursor:"pointer"}}>清空画布</button>}
        </div>
        <div style={{background:"#fafaf7",backgroundImage:"linear-gradient(rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.05) 1px, transparent 1px)",backgroundSize:"40px 40px",borderRadius:"24px",minHeight:"calc(100vh - 180px)",padding:"2rem",border:"2px dashed rgba(0,0,0,0.2)"}}>
          {gallery.length===0 && !loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px",color:"#bbb"}}>
              <div style={{fontSize:"3.5rem",marginBottom:"1rem",color:"#ddd"}}>◧</div>
              <div style={{fontSize:"0.95rem",color:"#999"}}>{t("image.empty")}</div>
              <div style={{fontSize:"0.8rem",color:"#bbb",marginTop:"0.5rem"}}>{t("image.emptyTip")}</div>
            </div>
          )}
          {loading && (
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"500px"}}>
              <div style={{width:"40px",height:"40px",border:"3px solid #eee",borderTopColor:"#0d0d0d",borderRadius:"50%",animation:"spin 1s linear infinite"}}></div>
              <div style={{marginTop:"1rem",color:"#888",fontSize:"0.9rem"}}>{t("image.creating")}</div>
              <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
            </div>
          )}
          {gallery.length>0 && (
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))",gap:"1rem"}}>
              {gallery.map((item,i)=>(
                <div key={i} style={{borderRadius:"14px",overflow:"hidden",background:"#fff",position:"relative",aspectRatio:"1",boxShadow:"0 4px 12px rgba(0,0,0,0.04)",cursor:"pointer"}}
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
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.model")}</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.4rem"}}>
            {MODELS.map(m=>(
              <button key={m.key} onClick={()=>setModel(m.key)}
                style={{textAlign:"left",padding:"0.7rem 0.9rem",border:model===m.key?"2px solid #0d0d0d":"1px solid #e5e5e5",background:model===m.key?"#f9f7f2":"#fff",borderRadius:"10px",cursor:"pointer"}}>
                <div style={{fontSize:"0.88rem",fontWeight:500,color:"#0d0d0d"}}>{t(`image.models.${m.labelKey}`)}</div>
                <div style={{fontSize:"0.72rem",color:"#888",marginTop:"0.15rem"}}>{t(`image.models.${m.descKey}`)}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.reference")}</div>
          <div style={{display:"flex",gap:"0.4rem",flexWrap:"wrap"}}>
            {refPreviews.map((p,i)=>(
              <div key={i} style={{position:"relative",width:60,height:60}}>
                <img src={p} style={{width:"100%",height:"100%",objectFit:"cover",borderRadius:8}}/>
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
            <option value="1024x1024">{t("image.sizeSquare")}</option>
            <option value="768x1024">{t("image.sizePortrait")}</option>
            <option value="1024x768">{t("image.sizeLandscape")}</option>
          </select>
        </div>
        <div style={{flex:1,display:"flex",flexDirection:"column"}}>
          <div style={{fontSize:"0.72rem",color:"#999",textTransform:"uppercase",letterSpacing:"0.1em",marginBottom:"0.6rem"}}>{t("image.section.prompt")}</div>
          <textarea value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder={refImages.length>0?t("image.promptWithRef"):t("image.promptPlaceholder")}
            style={{width:"100%",padding:"0.75rem 0.9rem",border:"1px solid #e5e5e5",borderRadius:"12px",fontSize:"0.88rem",minHeight:"120px",resize:"vertical",fontFamily:"inherit",background:"#fff",color:"#333",flex:1}}/>
        </div>
        {msg && <div style={{color:"#0a0",background:"#eaf7ea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{msg}</div>}
        {error && <div style={{color:"#c00",background:"#ffeaea",padding:"0.7rem",borderRadius:"10px",fontSize:"0.8rem"}}>{error}</div>}
        <button onClick={generate}
          style={{padding:"0.9rem",background:"#0d0d0d",color:"#fff",border:"none",borderRadius:"12px",cursor:loading?"wait":"pointer",fontSize:"0.95rem",fontWeight:500}}>
          {refImages.length>0?t("image.generateRef"):t("image.generate")}
        </button>
      </aside>
    </div>
  );
}
