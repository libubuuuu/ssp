"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLang } from "@/lib/i18n/LanguageContext";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

type Step = "request" | "verify" | "success";

export default function ForgotPasswordPage() {
  const { t } = useLang();
  const router = useRouter();
  const [step, setStep] = useState<Step>("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (countdown <= 0) return;
    const id = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(id);
  }, [countdown]);

  useEffect(() => {
    if (step !== "success") return;
    const id = setTimeout(() => router.push("/auth"), 2000);
    return () => clearTimeout(id);
  }, [step, router]);

  const requestCode = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/auth/send-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, purpose: "reset" }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : t("errors.networkError"));
      }
      setCountdown(60);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.networkError"));
      return false;
    } finally {
      setLoading(false);
    }
  };

  const handleRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      setError(t("auth.inputEmailFirst"));
      return;
    }
    const ok = await requestCode();
    if (ok) setStep("verify");
  };

  const handleResend = async () => {
    if (countdown > 0 || loading) return;
    await requestCode();
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 6) {
      setError(t("auth.forgot.passwordTooShort"));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t("auth.forgot.passwordMismatch"));
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/reset-password-by-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, new_password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail : t("errors.networkError"));
      }
      setStep("success");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.networkError"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-[80vh] flex items-center justify-center px-4">
      <div className="w-full max-w-md p-8 rounded-xl bg-zinc-900 border border-zinc-800">
        <h1 className="text-2xl font-bold text-center mb-2">{t("auth.forgot.title")}</h1>
        <p className="text-zinc-400 text-center mb-8">
          {step === "request" && t("auth.forgot.requestTip")}
          {step === "verify" && t("auth.forgot.verifyTip")}
          {step === "success" && t("auth.forgot.successTip")}
        </p>

        {step === "request" && (
          <form onSubmit={handleRequest} className="space-y-4">
            <div>
              <label className="block text-sm text-zinc-400 mb-2">{t("auth.forgot.emailLabel")}</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                required
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-900/20 border border-red-700">
                <p className="text-red-400 text-sm">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {loading ? t("auth.forgot.sending") : t("auth.sendCode")}
            </button>
          </form>
        )}

        {step === "verify" && (
          <form onSubmit={handleReset} className="space-y-4">
            <div className="text-zinc-400 text-sm">
              {t("auth.forgot.codeSentTo")} <span className="text-amber-400">{email}</span>
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">{t("auth.forgot.codeLabel")}</label>
              <input
                type="text"
                inputMode="numeric"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder={t("auth.codePlaceholder")}
                maxLength={6}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none text-center tracking-[0.3rem] font-mono text-lg"
                required
              />
              <button
                type="button"
                onClick={handleResend}
                disabled={countdown > 0 || loading}
                className="mt-2 text-xs text-amber-400 hover:text-amber-300 disabled:text-zinc-600 disabled:cursor-default"
              >
                {countdown > 0 ? `${countdown}s · ${t("auth.resend")}` : t("auth.resend")}
              </button>
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">{t("auth.forgot.newPasswordLabel")}</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                required
                minLength={6}
              />
            </div>
            <div>
              <label className="block text-sm text-zinc-400 mb-2">{t("auth.forgot.confirmPasswordLabel")}</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-lg bg-zinc-800 border border-zinc-700 focus:border-amber-500 outline-none"
                required
                minLength={6}
              />
            </div>

            {error && (
              <div className="p-3 rounded-lg bg-red-900/20 border border-red-700">
                <p className="text-red-400 text-sm">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || code.length !== 6}
              className="w-full py-3 rounded-lg bg-amber-500 text-black font-medium hover:bg-amber-400 disabled:opacity-50 transition-colors"
            >
              {loading ? t("auth.forgot.submitting") : t("auth.forgot.submit")}
            </button>
          </form>
        )}

        {step === "success" && (
          <div className="text-center space-y-4">
            <div className="text-6xl">✅</div>
            <p className="text-zinc-400 text-sm">{t("auth.forgot.successDetail")}</p>
          </div>
        )}

        <div className="mt-6 text-center">
          <Link
            href="/auth"
            className="text-sm text-zinc-400 hover:text-amber-400 transition-colors"
          >
            {t("auth.forgot.backToLogin")}
          </Link>
        </div>
      </div>
    </div>
  );
}
