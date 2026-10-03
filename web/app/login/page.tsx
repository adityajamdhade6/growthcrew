"use client";

import { useState } from "react";
import { api, ApiError, setToken } from "@/lib/api";
import { Button, ErrorState, Field, inputClass } from "@/components/ui";

// Set at build time for a public demo only; the demo account sees sample data.
const DEMO_EMAIL = process.env.NEXT_PUBLIC_DEMO_EMAIL;
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD;

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ token: string }>("/auth/login", {
        method: "POST",
        body: { email, password },
      });
      setToken(result.token);
      window.location.assign("/");
    } catch (caught) {
      setError(caught as ApiError);
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-surface p-6">
        <div>
          <p className="text-lg font-semibold tracking-tight">GrowthCrew</p>
          <p className="mt-1 text-sm text-muted">Sign in to review what your team has drafted.</p>
        </div>
        {error && <ErrorState error={error} />}
        <Field label="Email">
          <input
            className={inputClass}
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field label="Password">
          <input
            className={inputClass}
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Button variant="primary" type="submit" busy={busy} className="w-full">
          Sign in
        </Button>
        {DEMO_EMAIL && DEMO_PASSWORD && (
          <Button type="button" className="w-full" onClick={() => { setEmail(DEMO_EMAIL); setPassword(DEMO_PASSWORD); }}>
            Fill in the demo login (sample data)
          </Button>
        )}
        <p className="text-xs text-muted">
          Accounts are created by an admin with <code>growthcrew user add</code>.
        </p>
      </form>
    </main>
  );
}
