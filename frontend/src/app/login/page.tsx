"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/components/auth-shell";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    void createClient().auth.getUser().then(({ data }) => {
      if (data.user) router.replace("/dashboard");
    });
  }, [router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);
    const { error: signInError } = await createClient().auth.signInWithPassword({ email, password });
    setLoading(false);
    if (signInError) return setError(signInError.message);
    router.replace("/dashboard");
    router.refresh();
  }

  return <AuthShell title="Welcome back" subtitle="Sign in to your private LeadForge workspace.">
    <form onSubmit={submit} className="space-y-4">
      <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" required autoComplete="email" />
      <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" required minLength={6} autoComplete="current-password" />
      {error && <p className="text-sm text-rose-600">{error}</p>}
      <Button type="submit" className="w-full" disabled={loading}>{loading ? "Signing in..." : "Sign in"}</Button>
    </form>
    <p className="mt-5 text-sm text-slate-600">New here? <Link className="font-semibold text-indigo-700" href="/signup">Create an account</Link></p>
  </AuthShell>;
}

