"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import LanguageSwitcher from "@/lib/i18n/LanguageSwitcher";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const { t } = useLang();
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [userName, setUserName] = useState("");
  const [credits, setCredits] = useState(0);
  const [showMenu, setShowMenu] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const user = localStorage.getItem("user");
    if (token && user) {
      setIsLoggedIn(true);
      try {
        const u = JSON.parse(user);
        setUserName(u.name || u.email || "");
        setCredits(u.credits || 0);
      } catch {}
    }
  }, []);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setIsLoggedIn(false);
    setShowMenu(false);
  };

  useEffect(() => {
    const cv = canvasRef.current;
    const wrap = wrapRef.current;
    if (!cv || !wrap) return;
    const ct = cv.getContext("2d");
    if (!ct) return;

    let W = 0, H = 0;
    let bgId: ImageData | null = null;
    let outId: ImageData | null = null;
    let fieldBuf: Float32Array | null = null;
    let ready = false;
    let tm = 0, mouseX = 0, mouseY = 0, hasMouse = false;
    let animId = 0;

    type Ball = { x:number;y:number;vx:number;vy:number;tx:number;ty:number;r:number;offA:number;offS:number;offD:number;follow:number;damp:number;main:boolean };
    const balls: Ball[] = [];
    const N = 5;

    function initBalls(){
      balls.length=0;
      const baseR=Math.min(W,H)*0.14;
      balls.push({x:W*0.5,y:H*0.55,vx:0,vy:0,tx:0,ty:0,r:baseR,offA:0,offS:0,offD:0,follow:0.025,damp:0.86,main:true});
      for(let i=1;i<N;i++){
        balls.push({
          x:W*0.5,y:H*0.55,vx:0,vy:0,tx:0,ty:0,
          r:baseR*(0.32+Math.random()*0.25),
          offA:Math.random()*Math.PI*2,
          offS:0.3+Math.random()*0.4,
          offD:0.03+Math.random()*0.05,
          follow:0.014+Math.random()*0.014,
          damp:0.87+Math.random()*0.04,
          main:false
        });
      }
    }

    function build(){
      const rect = wrap!.getBoundingClientRect();
      W = cv!.width = Math.max(400, Math.floor(rect.width));
      H = cv!.height = Math.floor(rect.height);
      ct!.fillStyle = "#edeae4";
      ct!.fillRect(0, 0, W, H);
      ct!.textAlign = "center";
      ct!.textBaseline = "middle";
      ct!.fillStyle = "#0d0d0d";
      let fs = Math.floor(W * 0.14);
      const fStack = `italic 700 SIZEpx "Didot","Bodoni 72","Big Caslon","Playfair Display",Georgia,"Times New Roman",serif`;
      ct!.font = fStack.replace("SIZE", String(fs));
      let mw = ct!.measureText("xiaoLi ai.").width;
      while (mw > W * 0.84 && fs > 30) {
        fs -= 3;
        ct!.font = fStack.replace("SIZE", String(fs));
        mw = ct!.measureText("xiaoLi ai.").width;
      }
      ct!.fillText("xiaoLi ai.", W / 2, H / 2);
      bgId = ct!.getImageData(0, 0, W, H);
      outId = ct!.createImageData(W, H);
      fieldBuf = new Float32Array(W * H);
      mouseX = W * 0.5;
      mouseY = H * 0.55;
      initBalls();
      ready = true;
    }

    function smpl(d: Uint8ClampedArray, x: number, y: number) {
      if (x < 0) x = 0; else if (x > W - 1.01) x = W - 1.01;
      if (y < 0) y = 0; else if (y > H - 1.01) y = H - 1.01;
      const i = ((y | 0) * W + (x | 0)) * 4;
      return [d[i], d[i + 1], d[i + 2]];
    }

    function frame() {
      animId = requestAnimationFrame(frame);
      if (!ready || !bgId || !outId || !fieldBuf) return;
      tm += 0.012;
      let mx, my;
      if (hasMouse) { mx = mouseX; my = mouseY; }
      else { mx = W * 0.5 + Math.sin(tm * 0.2) * W * 0.18; my = H * 0.5 + Math.sin(tm * 0.17) * H * 0.1; }
      for (let i = 0; i < N; i++) {
        const b = balls[i];
        if (b.main) { b.tx = mx; b.ty = my; }
        else {
          const orbA = b.offA + tm * b.offS;
          const orbR = Math.min(W, H) * b.offD;
          b.tx = balls[0].x + Math.cos(orbA) * orbR;
          b.ty = balls[0].y + Math.sin(orbA) * orbR;
        }
        b.vx = (b.vx + (b.tx - b.x) * b.follow) * b.damp;
        b.vy = (b.vy + (b.ty - b.y) * b.follow) * b.damp;
        b.x += b.vx; b.y += b.vy;
      }
      let mnX=W,mxX=0,mnY=H,mxY=0;
      for(let i=0;i<N;i++){
        const b=balls[i];const rr=b.r*2.2;
        if(b.x-rr<mnX)mnX=b.x-rr;if(b.x+rr>mxX)mxX=b.x+rr;
        if(b.y-rr<mnY)mnY=b.y-rr;if(b.y+rr>mxY)mxY=b.y+rr;
      }
      const xa=Math.max(0,mnX|0),xb=Math.min(W-1,(mxX+1)|0);
      const ya=Math.max(0,mnY|0),yb=Math.min(H-1,(mxY+1)|0);
      fieldBuf.fill(0);
      for(let py=ya;py<=yb;py++){
        const row=py*W;
        for(let px=xa;px<=xb;px++){
          let v=0;
          for(let i=0;i<N;i++){
            const b=balls[i];
            const dx=px-b.x,dy=py-b.y;
            const d2=dx*dx+dy*dy+1;
            v+=(b.r*b.r)/d2;
          }
          fieldBuf[row+px]=v;
        }
      }
      outId.data.set(bgId.data);
      const src=bgId.data,dst=outId.data;
      const TH=1.2;
      for(let py=ya;py<=yb;py++){
        const row=py*W;
        for(let px=xa;px<=xb;px++){
          const v=fieldBuf[row+px];
          if(v<TH)continue;
          let gx=0,gy=0;
          if(px>0&&px<W-1&&py>0&&py<H-1){
            gx=fieldBuf[row+px+1]-fieldBuf[row+px-1];
            gy=fieldBuf[row+W+px]-fieldBuf[row-W+px];
          }
          const gLen=Math.sqrt(gx*gx+gy*gy)+0.0001;
          const nx=gx/gLen,ny=gy/gLen;
          const vNorm=Math.min(1,(v-TH)*0.8);
          const domeZ=Math.sqrt(vNorm);
          const edgeNear=1-domeZ;
          const refrStr=18*edgeNear+2;
          const sx=px-nx*refrStr,sy=py-ny*refrStr;
          const ca=16*edgeNear;
          const pR=smpl(src,sx+nx*ca,sy+ny*ca);
          const pG=smpl(src,sx,sy);
          const pB=smpl(src,sx-nx*ca,sy-ny*ca);
          const ii=(py*W+px)*4;
          dst[ii]=pR[0];dst[ii+1]=pG[1];dst[ii+2]=pB[2];dst[ii+3]=255;
        }
      }
      ct!.putImageData(outId,0,0);
      const rw=xb-xa+1;
      const ii=ct!.getImageData(xa,ya,rw,yb-ya+1);
      const iid=ii.data;
      for(let py=ya;py<=yb;py++){
        for(let px=xa;px<=xb;px++){
          const v=fieldBuf[py*W+px];
          if(v<TH)continue;
          const vNorm=Math.min(1,(v-TH)*0.8);
          const domeZ=Math.sqrt(vNorm);
          const edgeNear=1-domeZ;
          let gx=0,gy=0;
          if(px>0&&px<W-1&&py>0&&py<H-1){
            gx=fieldBuf[py*W+px+1]-fieldBuf[py*W+px-1];
            gy=fieldBuf[(py+1)*W+px]-fieldBuf[(py-1)*W+px];
          }
          const gLen=Math.sqrt(gx*gx+gy*gy)+0.0001;
          const nx=gx/gLen,ny=gy/gLen;
          const lightDot=Math.max(0,-nx*0.55-ny*0.6)*domeZ;
          const highlight=Math.pow(lightDot,3)*255;
          const rim=Math.pow(edgeNear,2.5)*80;
          const shade=edgeNear*edgeNear*35;
          const idx=((py-ya)*rw+(px-xa))*4;
          iid[idx]=Math.min(255,Math.max(0,iid[idx]+highlight-shade));
          iid[idx+1]=Math.min(255,Math.max(0,iid[idx+1]+highlight-shade));
          iid[idx+2]=Math.min(255,Math.max(0,iid[idx+2]+highlight-shade+rim*0.2));
        }
      }
      ct!.putImageData(ii,xa,ya);
    }

    build();
    frame();
    const mm = (e: MouseEvent) => {
      const r = cv!.getBoundingClientRect();
      mouseX = e.clientX - r.left;
      mouseY = e.clientY - r.top;
      hasMouse = true;
    };
    const ml = () => { hasMouse = false; };
    const rs = () => { build(); };
    wrap.addEventListener("mousemove", mm);
    wrap.addEventListener("mouseleave", ml);
    window.addEventListener("resize", rs);
    return () => {
      cancelAnimationFrame(animId);
      wrap.removeEventListener("mousemove", mm);
      wrap.removeEventListener("mouseleave", ml);
      window.removeEventListener("resize", rs);
    };
  }, []);

  return (
    <div ref={wrapRef} style={{width:"100vw",height:"100vh",background:"#edeae4",position:"relative",overflow:"hidden",cursor:"none"}}>
      <canvas ref={canvasRef} style={{display:"block",width:"100%",height:"100%"}}/>
      <nav style={{position:"absolute",top:0,left:0,right:0,display:"flex",justifyContent:"space-between",alignItems:"center",padding:"1.5rem 3rem",zIndex:10}}>
        <span style={{fontSize:"0.9rem",color:"#333",letterSpacing:"0.15em",fontFamily:"Georgia,serif",fontStyle:"italic"}}>xiaoLi ai. v2</span>
        {isLoggedIn ? (
          <div style={{display:"flex",gap:"1rem",alignItems:"center",position:"relative"}}>
      <div style={{position:"fixed",top:"1rem",right:"1rem",zIndex:1000}}><LanguageSwitcher /></div>
            <button onClick={()=>router.push("/dashboard")} style={{background:"#0d0d0d",border:"none",color:"#fff",cursor:"pointer",fontSize:"0.85rem",padding:"0.55rem 1.6rem",borderRadius:"999px"}}>{t("landing.enterApp")}</button>
            <button onClick={()=>setShowMenu(!showMenu)} style={{background:"rgba(255,255,255,0.6)",border:"1px solid rgba(0,0,0,0.1)",color:"#333",cursor:"pointer",fontSize:"0.85rem",padding:"0.5rem 1rem",borderRadius:"999px",display:"flex",alignItems:"center",gap:"0.5rem"}}>
              <span style={{width:"24px",height:"24px",borderRadius:"50%",background:"#0d0d0d",color:"#fff",display:"flex",alignItems:"center",justifyContent:"center",fontSize:"0.7rem"}}>{userName.charAt(0).toUpperCase()}</span>
              {userName}
            </button>
            {showMenu && (
              <div style={{position:"absolute",top:"110%",right:0,background:"#fff",borderRadius:"12px",boxShadow:"0 10px 30px rgba(0,0,0,0.12)",padding:"0.5rem",minWidth:"200px",zIndex:20}}>
                <div style={{padding:"0.75rem 1rem",borderBottom:"1px solid #eee"}}>
                  <div style={{fontSize:"0.85rem",color:"#333",fontWeight:500}}>{userName}</div>
                  <div style={{fontSize:"0.75rem",color:"#888",marginTop:"0.25rem"}}>{credits} {t("landing.creditsLabel")}</div>
                </div>
                <button onClick={()=>{setShowMenu(false);router.push("/profile")}} style={{display:"block",width:"100%",textAlign:"left",padding:"0.6rem 1rem",background:"none",border:"none",color:"#333",cursor:"pointer",fontSize:"0.85rem",borderRadius:"8px"}}>{t("landing.profile")}</button>
                <button onClick={()=>{setShowMenu(false);router.push("/pricing")}} style={{display:"block",width:"100%",textAlign:"left",padding:"0.6rem 1rem",background:"none",border:"none",color:"#333",cursor:"pointer",fontSize:"0.85rem",borderRadius:"8px"}}>{t("landing.topup")}</button>
                <button onClick={logout} style={{display:"block",width:"100%",textAlign:"left",padding:"0.6rem 1rem",background:"none",border:"none",color:"#d00",cursor:"pointer",fontSize:"0.85rem",borderRadius:"8px",borderTop:"1px solid #eee",marginTop:"0.25rem"}}>{t("landing.logout")}</button>
              </div>
            )}
          </div>
        ) : (
          <button onClick={()=>router.push("/auth")} style={{background:"#0d0d0d",border:"none",color:"#fff",cursor:"pointer",fontSize:"0.85rem",padding:"0.55rem 1.6rem",borderRadius:"999px"}}>{t("landing.login")}</button>
        )}
      </nav>
      <div style={{position:"absolute",bottom:"2rem",left:0,right:0,textAlign:"center",color:"#666",fontSize:"0.8rem",letterSpacing:"0.1em",fontFamily:"sans-serif",pointerEvents:"none"}}>{t("landing.footer")}</div>
    </div>
  );
}
