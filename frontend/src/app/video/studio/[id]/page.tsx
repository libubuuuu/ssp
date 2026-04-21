"use client";
import React, { useState, useRef, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://43.134.71.189:8000";

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
        setSegments((data.segments || []).map((s: any) => ({
          index: s.index, start: s.start, duration: s.duration, url: s.fal_url || s.url,
        })));
        if (data.batch_results) {
          setBatchTasks(data.batch_results);
          if (data.final_url) {
            setFinalUrl(data.final_url);
            setStep(5);
          } else if ((data.batch_results || []).some((r: any) => r.status === "completed" || r.status === "running" || r.status === "pending")) {
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
      } catch (e: any) {
        setError(e.message || "加载项目失败");
      }
    })();
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
      if (!res.ok) throw new Error(data.detail || "上传失败");
      setSessionId(data.session_id);
      setDuration(data.duration);
      setMsg(`视频 ${data.duration}秒 / ${data.size_mb}MB 上传成功`);
      setStep(2);
    } catch (e: any) { setError(e.message); }
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
    } catch (e: any) { setError(e.message); }
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
    } catch (e: any) { setError(e.message); }
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
    } catch (e: any) { setError(e.message); }
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
      if (!res.ok) throw new Error(data.detail || "提交失败");
      setBatchTasks(data.tasks);
      setStep(4);
      setMsg(`已提交 ${data.total} 个任务，等待生成...`);
      startPolling();
    } catch (e: any) { setError(e.message); }
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
    } catch (e: any) { setError(e.message); }
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
          }}>← 返回项目列表</button>
          <div style={{ fontSize: "0.85rem", color: "#999" }}>视频创作</div>
          <h1 style={{ fontSize: "1.8rem", fontWeight: 400, margin: "0.3rem 0", fontFamily: "Georgia,serif" }}>长视频 <span style={{ fontStyle: "italic" }}>工作台</span></h1>
          <div style={{ fontSize: "0.85rem", color: "#999" }}>上传任意长度视频，自动拆分 → 逐段翻拍 → 拼接成新长视频</div>
        </div>

        {/* 步骤条 */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", margin: "1.5rem 0", fontSize: "0.85rem" }}>
          {badge(1)} <span style={s(1)}>上传视频</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(2)} <span style={s(2)}>拆分</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(3)} <span style={s(3)}>配置元素</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(4)} <span style={s(4)}>批量生成</span> <span style={{ color: "#ccc" }}>→</span>
          {badge(5)} <span style={s(5)}>完成</span>
        </div>

        {/* 消息/错误 */}
        {msg && <div style={{ padding: "0.7rem 1rem", background: "#f9f7f2", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{msg}</div>}
        {error && <div style={{ padding: "0.7rem 1rem", background: "#ffeaea", color: "#c00", borderRadius: 10, marginBottom: "1rem", fontSize: "0.88rem" }}>{error}</div>}

        {/* Step 1: 上传 */}
        {step === 1 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>第一步：上传长视频</h3>
            <input type="file" accept="video/*" onChange={e => setVideoFile(e.target.files?.[0] || null)} style={{ display: "block", margin: "1rem 0" }} />
            {videoFile && (
              <div style={{ margin: "1rem 0" }}>
                <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "0.5rem" }}>已选: {videoFile.name} ({(videoFile.size / 1024 / 1024).toFixed(1)}MB)</div>
                <video src={URL.createObjectURL(videoFile)} controls style={{ width: "100%", maxWidth: "500px", borderRadius: "12px", background: "#000" }} />
              </div>
            )}
            <button onClick={uploadVideo} disabled={loading || !videoFile} style={btnPrimary(loading || !videoFile)}>
              {loading ? "上传中..." : "上传并进入下一步"}
            </button>
          </div>
        )}

        {/* Step 2: 拆分 */}
        {step === 2 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>第二步：选择拆分时长</h3>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>原视频时长 {duration}秒，将按下面的时长拆成多段</div>
            <div style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}>
              {[5, 8, 10, 15].map(d => (
                <button key={d} onClick={() => setSegmentDuration(d)} style={{
                  padding: "0.7rem 1.2rem", border: segmentDuration === d ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: segmentDuration === d ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer",
                }}>{d}秒/段</button>
              ))}
            </div>
            <div style={{ fontSize: "0.85rem", color: "#999", marginBottom: "1rem" }}>
              预计拆分为 <b>{Math.ceil(duration / segmentDuration)}</b> 段
            </div>
            <button onClick={splitVideo} disabled={loading} style={btnPrimary(loading)}>
              {loading ? "拆分中..." : "开始拆分"}
            </button>
          </div>
        )}

        {/* Step 3: 配置元素 */}
        {step === 3 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>第三步：配置元素（模特/产品）</h3>
            <div style={{ fontSize: "0.85rem", color: "#666", marginBottom: "1rem" }}>
              已拆分 {segments.length} 段。添加 1-4 个元素，所有段落共用这些元素。
            </div>

            {/* 模式选择 */}
            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ fontSize: "0.8rem", color: "#999", marginBottom: "0.5rem" }}>生成模式</div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button onClick={() => setMode("o1")} style={{
                  padding: "0.8rem 1.2rem", border: mode === "o1" ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: mode === "o1" ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer", textAlign: "left", flex: 1,
                }}>
                  <div style={{ fontWeight: 600 }}>快速模式 (O1)</div>
                  <div style={{ fontSize: "0.75rem", color: "#888" }}>$0.06/秒 · 速度快 · 无口播</div>
                </button>
                <button onClick={() => setMode("o3")} style={{
                  padding: "0.8rem 1.2rem", border: mode === "o3" ? "2px solid #0d0d0d" : "1px solid #ddd",
                  background: mode === "o3" ? "#f9f7f2" : "#fff", borderRadius: 10, cursor: "pointer", textAlign: "left", flex: 1,
                }}>
                  <div style={{ fontWeight: 600 }}>高质量 (O3 Pro)</div>
                  <div style={{ fontSize: "0.75rem", color: "#888" }}>$0.168/秒 · 含中文口播 · 画质好</div>
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
                    <button onClick={() => removeElement(idx)} style={{ background: "none", border: "none", color: "#c00", cursor: "pointer" }}>✕ 删除</button>
                  )}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: "1rem" }}>
                  {/* 主图 */}
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>主图 * </div>
                    <label style={{ display: "block", width: 120, height: 120, border: "2px dashed #ccc", borderRadius: 10, cursor: "pointer", overflow: "hidden", background: "#fafaf7" }}>
                      <input type="file" accept="image/*" style={{ display: "none" }}
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleMainImage(idx, f); }} />
                      {elem.main_preview
                        ? <img src={elem.main_preview} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                        : <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#999", fontSize: "0.8rem" }}>点击上传</div>}
                    </label>
                  </div>

                  {/* 参考图 */}
                  <div>
                    <div style={{ fontSize: "0.75rem", color: "#999", marginBottom: "0.3rem" }}>参考图 (0-3张，可选)</div>
                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      {elem.reference_previews.map((p, i) => (
                        <div key={i} style={{ position: "relative", width: 80, height: 80 }}>
                          <img src={p} style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 8 }} />
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
              <button onClick={addElement} style={{ padding: "0.7rem 1.5rem", border: "1px dashed #999", borderRadius: 10, background: "#fff", cursor: "pointer", marginBottom: "1rem" }}>+ 添加元素（还可加 {4 - elements.length} 个）</button>
            )}

            <button onClick={startBatch} disabled={loading} style={btnPrimary(loading)}>
              {loading ? "提交中..." : `开始批量生成 ${segments.length} 段`}
            </button>
          </div>
        )}

        {/* Step 4: 生成中 */}
        {step === 4 && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>第四步：批量生成中</h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: "1rem", margin: "1rem 0" }}>
              {batchTasks.map(t => (
                <div key={t.segment_index} style={{ padding: "1rem", border: "1px solid #eee", borderRadius: 10 }}>
                  <div style={{ fontSize: "0.8rem", color: "#666" }}>片段 #{t.segment_index + 1}</div>
                  <div style={{ fontSize: "0.85rem", margin: "0.5rem 0", fontWeight: 500,
                    color: t.status === "completed" ? "#0a0" : t.status === "failed" ? "#c00" : "#f80" }}>
                    {t.status === "completed" ? "✅ 完成" : t.status === "failed" ? "❌ 失败" : "⏳ 生成中..."}
                  </div>
                  {t.video_url && <video src={t.video_url} controls style={{ width: "100%", borderRadius: 8 }} />}
                  {t.error && <div style={{ fontSize: "0.75rem", color: "#c00" }}>{t.error}</div>}
                </div>
              ))}
            </div>
            {batchTasks.filter(t => t.status === "completed").length > 0 &&
             batchTasks.every(t => t.status === "completed" || t.status === "failed") && (
              <button onClick={mergeVideo} disabled={loading} style={btnPrimary(loading)}>
                {loading ? "拼接中..." : "拼接成最终视频"}
              </button>
            )}
          </div>
        )}

        {/* Step 5: 完成 */}
        {step === 5 && finalUrl && (
          <div style={{ background: "#fff", padding: "2rem", borderRadius: 16, border: "1px solid #eee" }}>
            <h3 style={{ marginTop: 0 }}>🎉 最终成品</h3>
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
