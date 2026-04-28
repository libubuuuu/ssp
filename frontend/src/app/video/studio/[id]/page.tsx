"use client";
import { useLang } from "@/lib/i18n/LanguageContext";
import React, { useState, useRef, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { adjustLocalUserCredits } from "@/lib/userState";
import { errMsg } from "@/lib/utils/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

interface Element {
  name: string;
  main_image_url: string;
  main_preview: string;
  reference_image_urls: string[];
  reference_previews: string[];
}

interface Segment {
  index: number;
  start: number;
  duration: number;
  url: string;
}

interface BatchTask {
  segment_index: number;
  task_id: string;
  endpoint_tag: string;
  status: string;
  video_url?: string;
  error?: string;
}

export default function VideoStudioDetailPage() {
  const { t } = useLang();
  const params = useParams();
  const router = useRouter();
  const urlSessionId = (params?.id as string) || "";
  const [step, setStep] = useState(1);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [duration, setDuration] = useState(0);
  const [segmentDuration, setSegmentDuration] = useState(8);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [elements, setElements] = useState<Element[]>([
    { name: "模特", main_image_url: "", main_preview: "", reference_image_urls: [], reference_previews: [] },
  ]);
  const [mode, setMode] = useState<"o1" | "o3">("o3");
  const [batchTasks, setBatchTasks] = useState<BatchTask[]>([]);
  const [finalUrl, setFinalUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  useEffect(() => {
    // 如果 URL 有 session_id，从后端恢复该项目状态
    if (!urlSessionId) return;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/studio/session/${urlSessionId}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        setSessionId(urlSessionId);
        setDuration(data.duration || 0);
        setSegments((data.segments || []).map((s: { index: number; start: number; duration: number; fal_url?: string; url?: string }) => ({
          index: s.index, start: s.start, duration: s.duration, url: s.fal_url || s.url,
        })));
        if (data.batch_results) {
          setBatchTasks(data.batch_results);
          if (data.final_url) {
            setFinalUrl(data.final_url);
            setStep(5);
          } else if ((data.batch_results || []).some((r: { status?: string }) => r.status === "completed" || r.status === "running" || r.status === "pending")) {
            setStep(4);
            startPolling();
          } else {
            setStep(3);
          }
        } else if (data.segments && data.segments.length > 0) {
          setStep(3);
        } else {
          setStep(2);
        }
      } catch (e) {
        setError(e.message || "加载项目失败");
      }
    })();
    // startPolling 定义在下方,引用在内部 IIFE 里;effect 只在 urlSessionId 变化时重跑,
    // 把 startPolling 放进 deps 会因为它每渲染重生而无限循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSessionId]);

  const token = () => localStorage.getItem("token") || "";

  // Step 1: 上传视频
  const uploadVideo = async () => {
    if (!videoFile) { setError("请先选择视频"); return; }
    setError(""); setLoading(true); setMsg("正在上传视频...");
    try {
      const fd = new FormData();
      fd.append("file", videoFile);
      const res = await fetch(`${API_BASE}/api/studio/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t("errors.uploadFailed"));
      setSessionId(data.session_id);
      setDuration(data.duration);
      setMsg(`视频 ${data.duration}秒 / ${data.size_mb}MB 上传成功`);
      setStep(2);
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  // Step 2: 拆分视频
  const splitVideo = async () => {
    setError(""); setLoading(true); setMsg("正在拆分视频（可能需要1-2分钟）...");
    try {
      const fd = new FormData();
      fd.append("session_id", sessionId);
      fd.append("segment_duration", String(segmentDuration));
      const res = await fetch(`${API_BASE}/api/studio/split`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "拆分失败");
      setSegments(data.segments);
      setMsg(`已拆分为 ${data.total_segments} 段`);
      setStep(3);
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  // 上传单张图片到 fal（通过后端代理）
  const uploadImage = async (file: File): Promise<string> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API_BASE}/api/video/upload/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token()}` },
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "图片上传失败");
    return data.url;
  };

  const handleMainImage = async (idx: number, file: File) => {
    setError(""); setLoading(true);
    try {
      const url = await uploadImage(file);
      const preview = URL.createObjectURL(file);
      setElements(prev => {
        const copy = [...prev];
        copy[idx] = { ...copy[idx], main_image_url: url, main_preview: preview };
        return copy;
      });
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  const handleRefImage = async (idx: number, file: File) => {
    setError(""); setLoading(true);
    try {
      const url = await uploadImage(file);
      const preview = URL.createObjectURL(file);
      setElements(prev => {
        const copy = [...prev];
        copy[idx] = {
          ...copy[idx],
          reference_image_urls: [...copy[idx].reference_image_urls, url],
          reference_previews: [...copy[idx].reference_previews, preview],
        };
        return copy;
      });
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  const removeRefImage = (elemIdx: number, refIdx: number) => {
    setElements(prev => {
      const copy = [...prev];
      copy[elemIdx] = {
        ...copy[elemIdx],
        reference_image_urls: copy[elemIdx].reference_image_urls.filter((_, i) => i !== refIdx),
        reference_previews: copy[elemIdx].reference_previews.filter((_, i) => i !== refIdx),
      };
      return copy;
    });
  };

  const addElement = () => {
    if (elements.length >= 4) { setError("最多 4 个元素"); return; }
    setElements(prev => [...prev, {
      name: `元素${prev.length + 1}`,
      main_image_url: "", main_preview: "",
      reference_image_urls: [], reference_previews: [],
    }]);
  };

  const removeElement = (idx: number) => {
    if (elements.length <= 1) return;
    setElements(prev => prev.filter((_, i) => i !== idx));
  };

  const updateElementName = (idx: number, name: string) => {
    setElements(prev => {
      const copy = [...prev];
      copy[idx] = { ...copy[idx], name };
      return copy;
    });
  };

  // Step 4: 开始批量生成
  const startBatch = async () => {
    const validElems = elements.filter(e => e.main_image_url);
    if (validElems.length === 0) { setError("至少要有一个元素（含主图）"); return; }
    setError(""); setLoading(true); setMsg("正在提交批量任务...");
    try {
      const res = await fetch(`${API_BASE}/api/studio/batch-generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}` },
        body: JSON.stringify({
          session_id: sessionId,
          segments,
          elements: validElems.map(e => ({
            name: e.name,
            main_image_url: e.main_image_url,
            reference_image_urls: e.reference_image_urls,
          })),
          mode,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || t("errors.submitFailed"));
      if (typeof data.cost === "number" && data.cost > 0) adjustLocalUserCredits(-data.cost);
      setBatchTasks(data.tasks);
      setStep(4);
      const failedNote = data.submit_failed ? `(${data.submit_failed} 段提交失败已退款)` : "";
      setMsg(`已提交 ${data.total} 个任务，等待生成...${failedNote}`);
      startPolling();
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/studio/batch-status/${sessionId}`, {
          headers: { Authorization: `Bearer ${token()}` },
        });
        const data = await res.json();
        if (res.ok) {
          setBatchTasks(data.tasks);
          if (typeof data.refunded_this_call === "number" && data.refunded_this_call > 0) {
            adjustLocalUserCredits(+data.refunded_this_call);
          }
          setMsg(`进度: ${data.completed}/${data.total} 完成, ${data.processing} 生成中, ${data.failed} 失败`);
          if (data.completed + data.failed === data.total) {
            if (pollRef.current) clearInterval(pollRef.current);
            if (data.completed > 0) setMsg(`全部完成！点击"拼接视频"生成最终成品`);
          }
        }
      } catch {}
    }, 5000);
  };

  // Step 5: 拼接
  const mergeVideo = async () => {
    setError(""); setLoading(true); setMsg("正在拼接视频（可能需要1-2分钟）...");
    try {
      const res = await fetch(`${API_BASE}/api/studio/merge/${sessionId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token()}` },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "拼接失败");
      setFinalUrl(data.final_url);
      setMsg(`拼接完成！共 ${data.segments_merged} 段`);
      setStep(5);
    } catch (e) { setError(errMsg(e)); }
    finally { setLoading(false); }
  };

  const s = (n: number) => step === n ? { opacity: 1 } : { opacity: 0.4 };
  const badge = (n: number) => (
    <div style={{ width: 24, height: 24, borderRadius: "50%", background: step >= n ? "#0d0d0d" : "#ddd", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600 }}>{n}</div>
  );

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#edeae4", fontFamily: "-apple-system,BlinkMacSystemFont,sans-serif" }}>
      <Sidebar />
      <main style={{ flex: 1, padding: "2rem 2.5rem", overflowY: "auto" }}>
        <div style={{ marginBottom: "1.5rem" }}>
          <button onClick={() => router.push("/video/studio")} style={{
            background: "none", border: "none", color: "#666", fontSize: "0.85rem", cursor: "pointer",
            padding: 0, marginBottom: "0.5rem",
          }}>{t("studio.detailBackToList")}</button>
          <div style={{ fontSize: "0.85rem", color: "#999" }}>{t("studio.studioVideo")}</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: "0.3rem 0", fontFamily: "Georgia,serif" }}>{t("studio.studioLongMain")} <span style={{ fontStyle: "italic" }}>工作台</span></h1>
          <div style={{ fontSize: "0.85rem", color: "#999" }}>{t("studio.detailSubtitle")}</div>
        </div>

        {/* 步骤条 */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", margin: "1.5rem 0", fontSize: "0.85rem" }}>
          {badge(1)} <span style={s(1)}>{t("studio.stepBadge1")}</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(2)} <span style={s(2)}>{t("studio.stepBadge2")}</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(3)} <span style={s(3)}>{t("studio.stepBadge3")}</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(4)} <span style={s(4)}>{t("studio.stepBadge4")}</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(5)} <span style={s(5)}>{t("studio.stepBadge5")}</span>
        </div>

        {/* 消息/错误 */}
        {msg && <div style={{ padding: "0.7rem 1rem", background: "#f9f7f2", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{msg}</div>}
        {error && <div style={{ padding: "0.7rem 1rem", background: "#ffeaea", color: "#c00", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{error}</div>}

        {/* Step 1: 上传 */}
        {step === 1 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>{t("studio.step1Title")}</h3>
            <input type="file" accept="video/*" onChange={e => setVideoFile(e.target.files?.[0] || null)} style={{ display: "block", margin: "1rem 0" }} />
            {videoFile && (
              <div style={{ margin: "1rem 0" }}>
                <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>{t("studio.chosen")}: {videoFile.name} ({(videoFile.size / 1024 / 1024).toFixed(1)}MB)</div>
                <video src={URL.createObjectURL(videoFile)} controls style={{ width: "100%", maxWidth: "500px", borderRadius: "12px", background: "#000" }} />
              </div>
            )}
            <button onClick={uploadVideo} disabled={loading || !videoFile} style={btnPrimary(loading || !videoFile)}>
              {loading ? t("studio.uploading") : t("studio.step1Btn")}
            </button>
          </div>
        )}

        {/* Step 2: 拆分 */}
        {step === 2 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ marginTop: 0 }}>{t("studio.step2Title")}</h3>
              <button onClick={() => setStep(1)} style={{ background: "none", border: "1px solid #ddd", borderRadius: 8, padding: "0.3rem 0.8rem", fontSize: "0.8rem", cursor: "pointer", color: "#666" }}>{t("studio.reUpload")}</button>
            </div>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>{t("studio.srcDurationIs")} {duration}{t("studio.splitByDuration")}</div>
            <div style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}>
              {[5, 8, 10, 15].map(d => (
                <button key={d} onClick={() => setSegmentDuration(d)} style={{
                  padding: "0.7rem 1.2rem", border: segmentDuration === d ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: segmentDuration === d ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer",
                }}>{d}{t("studio.secPerSeg")}</button>
              ))}
            </div>
            <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "1rem" }}>
              {t("studio.estimatedSplit")} <b>{Math.ceil(duration / segmentDuration)}</b> {t("studio.segsUnit2")}
            </div>
            <button onClick={splitVideo} disabled={loading} style={btnPrimary(loading)}>
              {loading ? t("studio.splitting") : t("studio.startSplit")}
            </button>
          </div>
        )}

        {/* Step 3: 配置元素 */}
        {step === 3 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ marginTop: 0 }}>{t("studio.step3Title")}</h3>
              <button onClick={() => setStep(2)} style={{ background: "none", border: "1px solid #ddd", borderRadius: 8, padding: "0.3rem 0.8rem", fontSize: "0.8rem", cursor: "pointer", color: "#666" }}>{t("studio.reSplit")}</button>
            </div>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>
              {t("studio.step3Desc1")} {segments.length} {t("studio.step3Desc2")}
            </div>

            {/* 模式选择 */}
            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.8rem", color: "#999", marginBottom: "0.5rem" }}>{t("studio.genMode")}</div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button onClick={() => setMode("o1")} style={{
                  padding: "0.8rem 1.2rem", border: mode === "o1" ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: mode === "o1" ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer", textAlign: "left", flex: 1,
                }}>
                  <div style={{ fontWeight: 600 }}>{t("studio.fastMode")}</div>
                  <div style={{ fontSize: "0.75rem", color: "#888" }}>{t("studio.fastDesc")}</div>
                </button>
                <button onClick={() => setMode("o3")} style={{
                  padding: "0.8rem 1.2rem", border: mode === "o3" ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: mode === "o3" ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer", textAlign: "left", flex: 1,
                }}>
                  <div style={{ fontWeight: 600 }}>{t("studio.highMode")}</div>
                  <div style={{ fontSize: "0.75rem", color: "#888" }}>{t("studio.highDesc")}</div>
                </button>
              </div>
            </div>

            {elements.map((elem, idx) => (
              <div key={idx} style={{ border: "1px solid #e5e5e5", borderRadius: 12, padding: "1rem", marginBottom: "1rem" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.8rem" }}>
                  <input value={elem.name} onChange={e => updateElementName(idx, e.target.value)}
                    style={{ padding: "0.4rem 0.7rem", border: "1px solid #ddd", borderRadius: 8, fontSize: "0.9rem", flex: 1 }}
                  />
                  {elements.length > 1 && (
                    <button onClick={() => removeElement(idx)} style={{ background: "none", border: "none", color: "#c00", cursor: "pointer" }}>{t("studio.deleteEl")}</button>
                  )}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: "1rem" }}>
                  {/* 主图 */}
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>{t("studio.mainImg")}</div>
                    <label style={{ display: "block", width: 120, height: 120, border: "2px dashed #ccc", borderRadius: 10, cursor: "pointer", overflow: "hidden", background: "#fafaf7" }}>
                      <input type="file" accept="image/*" style={{ display: "none" }}
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleMainImage(idx, f); }} />
                      {elem.main_preview
                        ? <img src={elem.main_preview} alt="元素主图" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                        : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999", fontSize: "0.8rem" }}>点击上传</div>}
                    </label>
                  </div>

                  {/* 参考图 */}
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>{t("studio.refImg")}</div>
                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      {elem.reference_previews.map((p, i) => (
                        <div key={i} style={{ position: "relative", width: 80, height: 80 }}>
                          <img src={p} alt="参考图" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 8 }} />
                          <button onClick={() => removeRefImage(idx, i)} style={{ position: "absolute", top: -6, right: -6, width: 18, height: 18, borderRadius: "50%", background: "#c00", color: "#fff", border: "none", cursor: "pointer", fontSize: "0.7rem" }}>×</button>
                        </div>
                      ))}
                      {elem.reference_image_urls.length < 3 && (
                        <label style={{ width: 80, height: 80, border: "2px dashed #ccc", borderRadius: 8, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: "#999", fontSize: "0.8rem", background: "#fafaf7" }}>
                          <input type="file" accept="image/*" style={{ display: "none" }}
                            onChange={e => { const f = e.target.files?.[0]; if (f) handleRefImage(idx, f); }} />
                          ＋
                        </label>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}

            {elements.length < 4 && (
              <button onClick={addElement} style={{ padding: "0.7rem 1.5rem", border: "1px dashed #999", borderRadius: 10, background: "#fff", cursor: "pointer", marginBottom: "1rem" }}>{t("studio.addElement")}（还可加 {4 - elements.length} 个）</button>
            )}

            <button onClick={startBatch} disabled={loading} style={btnPrimary(loading)}>
              {loading ? t("studio.submitting") : `${t("studio.startBatch")} ${segments.length}${t("studio.segsUnit2")}`}
            </button>
          </div>
        )}

        {/* Step 4: 生成中 */}
        {step === 4 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>{t("studio.step4Title")}</h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: "1rem", margin: "1rem 0" }}>
              {batchTasks.map(task => (
                <div key={task.segment_index} style={{ padding: "1rem", border: "1px solid #eee", borderRadius: 10 }}>
                  <div style={{ fontSize: "0.8rem", color: "#666" }}>{t("studio.segment")} #{task.segment_index + 1}</div>
                  <div style={{ fontSize: "0.85rem", margin: "0.5rem 0", fontWeight: 500,
                    color: task.status === "completed" ? "#0a0" : task.status === "failed" ? "#c00" : "#f80" }}>
                    {task.status === "completed" ? t("studio.completed") : task.status === "failed" ? t("studio.failed") : t("studio.generating2")}
                  </div>
                  {task.video_url && <video src={task.video_url} controls style={{ width: "100%", borderRadius: 8 }} />}
                  {task.error && <div style={{ fontSize: "0.75rem", color: "#c00" }}>{task.error}</div>}
                </div>
              ))}
            </div>
            {batchTasks.filter(it => it.status === "completed").length > 0 &&
             batchTasks.every(it => it.status === "completed" || it.status === "failed") && (
              <button onClick={mergeVideo} disabled={loading} style={btnPrimary(loading)}>
                {loading ? t("studio.merging") : t("studio.mergeFinal")}
              </button>
            )}
          </div>
        )}

        {/* Step 5: 完成 */}
        {step === 5 && finalUrl && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>{t("studio.step5Title")}</h3>
            <video src={finalUrl} controls style={{ width: "100%", maxWidth: 600, borderRadius: 12, margin: "1rem 0" }} />
            <div>
              <a href={finalUrl} download target="_blank" style={{ padding: "0.7rem 1.5rem", background: "#0d0d0d", color: "#fff", borderRadius: 10, textDecoration: "none", display: "inline-block" }}>下载视频</a>
              <button onClick={() => { setStep(1); setSessionId(""); setSegments([]); setBatchTasks([]); setFinalUrl(""); setMsg(""); }} style={{ marginLeft: "1rem", padding: "0.7rem 1.5rem", border: "1px solid #ddd", borderRadius: 10, background: "#fff", cursor: "pointer" }}>新建项目</button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function btnPrimary(disabled: boolean) {
  return {
    padding: "0.9rem 1.8rem",
    background: disabled ? "#999" : "#0d0d0d",
    color: "#fff",
    border: "none",
    borderRadius: 10,
    cursor: disabled ? "wait" : "pointer",
    fontSize: "0.95rem",
    fontWeight: 500,
  };
}
