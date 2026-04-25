"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function ProfilePage(){
  const { t } = useLang();
  const router=useRouter();
  const [user,setUser]=useState<any>(null);
  const [name,setName]=useState("");
  const [curPwd,setCurPwd]=useState("");
  const [newPwd,setNewPwd]=useState("");
  const [nameMsg,setNameMsg]=useState("");
  const [pwdMsg,setPwdMsg]=useState("");
  const [nameErr,setNameErr]=useState("");
  const [pwdErr,setPwdErr]=useState("");

  useEffect(()=>{
    const token=localStorage.getItem("token");
    const u=localStorage.getItem("user");
    if(!token||!u){router.push("/auth");return;}
    try{
      const obj=JSON.parse(u);
      setUser(obj);
      setName(obj.name||"");
    }catch{}
  },[router]);

  const saveName=async()=>{
    setNameMsg("");setNameErr("");
    try{
      const token=localStorage.getItem("token")||"";
      const res=await fetch(`${API_BASE}/api/auth/me`,{
        method:"PUT",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({name}),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||t("errors.saveFailed"));
      const newUser={...user,name};
      setUser(newUser);
      localStorage.setItem("user",JSON.stringify(newUser));
      setNameMsg("✓ 昵称已更新");
      setTimeout(()=>setNameMsg(""),2000);
    }catch(e:any){setNameErr(e.message);}
  };

  const changePwd=async()=>{
    setPwdMsg("");setPwdErr("");
    if(newPwd.length<6){setPwdErr("新密码至少6位");return;}
    try{
      const token=localStorage.getItem("token")||"";
      const res=await fetch(`${API_BASE}/api/auth/change-password`,{
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":`Bearer ${token}`},
        body:JSON.stringify({current_password:curPwd,new_password:newPwd}),
      });
      const data=await res.json();
      if(!res.ok)throw new Error(data.detail||t("errors.modifyFailed"));
      setPwdMsg("✓ 密码已修改");
      setCurPwd("");setNewPwd("");
      setTimeout(()=>setPwdMsg(""),2000);
    }catch(e:any){setPwdErr(e.message);}
  };

  const logout=()=>{
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    router.push("/");
  };

  if(!user)return <div style={{minHeight:"100vh",background:"#edeae4"}}/>;

  return (
    <div style={{display:"flex",minHeight:"100vh",background:"#edeae4",fontFamily:"-apple-system,BlinkMacSystemFont,sans-serif"}}>
      <Sidebar/>
      <main style={{flex:1,padding:"3rem 4rem",overflowY:"auto",maxWidth:"800px"}}>
        <div style={{marginBottom:"2.5rem"}}>
          <div style={{fontSize:"0.85rem",color:"#999",marginBottom:"0.3rem"}}>{t("profile.accountSettings")}</div>
          <h1 style={{fontSize:"2rem",fontWeight:300,color:"#0d0d0d",margin:0,fontFamily:"Georgia,serif"}}>{t("profile.titleMain")} <span style={{fontStyle:"italic"}}>{t("profile.titleAccent")}</span></h1>
        </div>

        <div style={{background:"#fff",borderRadius:"20px",padding:"2rem",marginBottom:"1.25rem",border:"1px solid rgba(0,0,0,0.04)"}}>
          <div style={{display:"flex",alignItems:"center",gap:"1.25rem",marginBottom:"1.5rem"}}>
            <div style={{width:"64px",height:"64px",borderRadius:"50%",background:"#0d0d0d",color:"#fff",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"1.5rem",fontWeight:500}}>
              {(user.name||user.email||"?").charAt(0).toUpperCase()}
            </div>
            <div style={{flex:1}}>
              <div style={{fontSize:"1.2rem",fontWeight:500,color:"#0d0d0d",marginBottom:"0.25rem"}}>{user.name||t("profile.noName")}</div>
              <div style={{fontSize:"0.85rem",color:"#888"}}>{user.email}</div>
            </div>
          </div>
          <div style={{display:"flex",gap:"2rem",paddingTop:"1.25rem",borderTop:"1px solid #f0ede8"}}>
            <div>
              <div style={{fontSize:"1.6rem",fontWeight:300,color:"#0d0d0d"}}>{user.credits||0}</div>
              <div style={{fontSize:"0.75rem",color:"#888"}}>{t("profile.availableCredits")}</div>
            </div>
            <div>
              <div style={{fontSize:"1.6rem",fontWeight:300,color:"#0d0d0d"}}>{user.role==="admin"?t("profile.roleAdmin"):t("profile.roleUser")}</div>
              <div style={{fontSize:"0.75rem",color:"#888"}}>{t("profile.accountType")}</div>
            </div>
            <button onClick={()=>router.push("/pricing")} style={{marginLeft:"auto",alignSelf:"center",background:"#0d0d0d",color:"#fff",border:"none",padding:"0.65rem 1.4rem",borderRadius:"999px",cursor:"pointer",fontSize:"0.85rem"}}>{t("profile.topupCredits")}</button>
          </div>
        </div>

        <div style={{background:"#fff",borderRadius:"20px",padding:"2rem",marginBottom:"1.25rem",border:"1px solid rgba(0,0,0,0.04)"}}>
          <div style={{fontSize:"1rem",fontWeight:500,color:"#0d0d0d",marginBottom:"1.25rem"}}>{t("profile.editName")}</div>
          <div style={{marginBottom:"0.75rem"}}>
            <input value={name} onChange={e=>setName(e.target.value)} placeholder={t("profile.namePlaceholder")}
              style={{width:"100%",padding:"0.75rem 1rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.9rem",background:"#fafaf7 !important",color:"#333 !important",boxSizing:"border-box"}}/>
          </div>
          {nameErr && <div style={{color:"#c00",fontSize:"0.8rem",marginBottom:"0.75rem"}}>{nameErr}</div>}
          {nameMsg && <div style={{color:"#0a7",fontSize:"0.8rem",marginBottom:"0.75rem"}}>{nameMsg}</div>}
          <button onClick={saveName} style={{padding:"0.65rem 1.5rem",background:"#0d0d0d",color:"#fff",border:"none",borderRadius:"10px",cursor:"pointer",fontSize:"0.85rem"}}>{t("profile.saveChanges")}</button>
        </div>

        <div style={{background:"#fff",borderRadius:"20px",padding:"2rem",marginBottom:"1.25rem",border:"1px solid rgba(0,0,0,0.04)"}}>
          <div style={{fontSize:"1rem",fontWeight:500,color:"#0d0d0d",marginBottom:"1.25rem"}}>{t("profile.editPassword")}</div>
          <div style={{display:"flex",flexDirection:"column",gap:"0.75rem",marginBottom:"1rem"}}>
            <div>
              <label style={{display:"block",fontSize:"0.75rem",color:"#999",marginBottom:"0.35rem"}}>{t("profile.currentPassword")}</label>
              <input type="password" value={curPwd} onChange={e=>setCurPwd(e.target.value)} placeholder={t("profile.currentPasswordPH")}
                style={{width:"100%",padding:"0.75rem 1rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.9rem",background:"#fafaf7 !important",color:"#333 !important",boxSizing:"border-box"}}/>
            </div>
            <div>
              <label style={{display:"block",fontSize:"0.75rem",color:"#999",marginBottom:"0.35rem"}}>{t("profile.newPassword")}</label>
              <input type="password" value={newPwd} onChange={e=>setNewPwd(e.target.value)} placeholder={t("profile.newPasswordPH")}
                style={{width:"100%",padding:"0.75rem 1rem",border:"1px solid #e5e5e5",borderRadius:"10px",fontSize:"0.9rem",background:"#fafaf7 !important",color:"#333 !important",boxSizing:"border-box"}}/>
            </div>
          </div>
          {pwdErr && <div style={{color:"#c00",fontSize:"0.8rem",marginBottom:"0.75rem"}}>{pwdErr}</div>}
          {pwdMsg && <div style={{color:"#0a7",fontSize:"0.8rem",marginBottom:"0.75rem"}}>{pwdMsg}</div>}
          <button onClick={changePwd} style={{padding:"0.65rem 1.5rem",background:"#0d0d0d",color:"#fff",border:"none",borderRadius:"10px",cursor:"pointer",fontSize:"0.85rem"}}>{t("profile.confirmChange")}</button>
        </div>

        {user.role === "admin" && (
          <div onClick={()=>router.push("/profile/2fa")} style={{background:"#fff",borderRadius:"20px",padding:"2rem",marginBottom:"1.25rem",border:"1px solid rgba(0,0,0,0.04)",cursor:"pointer"}}>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
              <div>
                <div style={{fontSize:"1rem",fontWeight:500,color:"#0d0d0d",marginBottom:"0.3rem"}}>🔐 {t("profile.twoFA")}</div>
                <div style={{fontSize:"0.85rem",color:"#888"}}>{t("profile.twoFADesc")}</div>
              </div>
              <div style={{color:"#999",fontSize:"1.2rem"}}>→</div>
            </div>
          </div>
        )}
        <div style={{background:"#fff",borderRadius:"20px",padding:"2rem",border:"1px solid rgba(0,0,0,0.04)"}}>
          <div style={{fontSize:"1rem",fontWeight:500,color:"#0d0d0d",marginBottom:"0.5rem"}}>{t("profile.logoutSection")}</div>
          <div style={{fontSize:"0.85rem",color:"#888",marginBottom:"1rem"}}>{t("profile.logoutTip")}</div>
          <button onClick={logout} style={{padding:"0.65rem 1.5rem",background:"none",color:"#c00",border:"1px solid #c00",borderRadius:"10px",cursor:"pointer",fontSize:"0.85rem"}}>{t("profile.logoutBtn")}</button>
        </div>
      </main>
    </div>
  );
}
