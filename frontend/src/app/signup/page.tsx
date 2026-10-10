"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/components/auth-shell";
import { createClient } from "@/lib/supabase/client";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState(""); const [message, setMessage] = useState(""); const [loading, setLoading] = useState(false);
  useEffect(() => { void createClient().auth.getUser().then(({ data }) => { if (data.user) router.replace("/dashboard"); }); }, [router]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setMessage(""); setLoading(true);
    const { data, error: signUpError } = await createClient().auth.signUp({ email, password, options: { emailRedirectTo: `${window.location.origin}/dashboard` } });
    setLoading(false);
    if (signUpError) return setError(signUpError.message);
    if (data.session) { router.replace("/dashboard"); router.refresh(); return; }
    setMessage("Check your email to confirm your account, then return here to sign in.");
  }
  return <AuthShell title="Create your workspace" subtitle="Your leads stay private to your account."><form onSubmit={submit} className="space-y-4"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoComplete="email" /><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 6 characters" required minLength={6} autoComplete="new-password" />{error && <p className="text-sm text-rose-600">{error}</p>}{message && <p className="text-sm text-emerald-700">{message}</p>}<Button type="submit" className="w-full" disabled={loading}>{loading ? "Creating account..." : "Create account"}</Button></form><p className="mt-5 text-sm text-slate-600">Already registered? <Link className="font-semibold text-indigo-700" href="/login">Sign in</Link></p></AuthShell>;
}
