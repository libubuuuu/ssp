"use client";
import AdminSidebar from "@/components/AdminSidebar";
import SystemHealthBanner from "@/components/SystemHealthBanner";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checking, setChecking] = useState(true);
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem("token");
    const userStr = localStorage.getItem("user");
    if (!token || !userStr) {
      router.push("/auth");
      return;
    }
    try {
      const u = JSON.parse(userStr);
      if (u.role !== "admin") {
        router.push("/dashboard");
        return;
      }
      setAuthorized(true);
    } catch {
      router.push("/auth");
    } finally {
      setChecking(false);
    }
  }, [router]);

  if (checking) {
    return (
      <div style={{ padding: "3rem", textAlign: "center", color: "#999" }}>
        Loading...
      </div>
    );
  }

  if (!authorized) return null;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "#f5f3ed" }}>
      <AdminSidebar />
      <main style={{ flex: 1, overflow: "auto" }}>
        <SystemHealthBanner />
        {children}
      </main>
    </div>
  );
}
