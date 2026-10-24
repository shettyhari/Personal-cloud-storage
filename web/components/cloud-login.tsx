"use client";
import { Cloud, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { sb } from "@/lib/cloud";
import { Button, Card, CopyBox, ErrorBox, Field, Input, Notice } from "./ui";

type Step = "loading" | "signin" | "signup" | "check-email" | "enroll" | "verify";

/** Hosted dashboard sign-in. Two-factor (TOTP) is mandatory before any server can be reached. */
export function CloudLogin() {
  const [step, setStep] = useState<Step>("loading");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [enroll, setEnroll] = useState<{ id: string; qr: string; secret: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const done = () => {
    const next = new URLSearchParams(location.search).get("next");
    location.href = next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/servers";
  };

  const afterPassword = async () => {
    const { data: aal } = await sb().auth.mfa.getAuthenticatorAssuranceLevel();
    if (aal?.currentLevel === "aal2") return done();
    const { data: factors } = await sb().auth.mfa.listFactors();
    const verified = (factors?.totp || []).find((f) => f.status === "verified");
    if (verified) { setStep("verify"); return; }
    for (const f of factors?.all || []) if (f.status !== "verified") await sb().auth.mfa.unenroll({ factorId: f.id });
    const { data, error: e } = await sb().auth.mfa.enroll({ factorType: "totp", friendlyName: "Personal Cloud" });
    if (e) throw e;
    setEnroll({ id: data.id, qr: data.totp.qr_code, secret: data.totp.secret });
    setStep("enroll");
  };

  useEffect(() => {
    sb().auth.getSession().then(({ data }) => (data.session ? afterPassword().catch(setError) : setStep("signin")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = (fn: () => Promise<void>) => async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try { await fn(); } catch (err) { setError(err); } finally { setBusy(false); }
  };

  const signIn = run(async () => {
    const { error: e } = await sb().auth.signInWithPassword({ email, password });
    if (e) throw e;
    await afterPassword();
  });
  const signUp = run(async () => {
    if (password.length < 12) throw new Error("Use at least 12 characters.");
    const { data, error: e } = await sb().auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/login` } });
    if (e) throw e;
    if (data.session) await afterPassword(); else setStep("check-email");
  });
  const verifyCode = run(async () => {
    const factorId = enroll?.id || (await sb().auth.mfa.listFactors()).data?.totp.find((f) => f.status === "verified")?.id;
    if (!factorId) throw new Error("No authenticator found");
    const { error: e } = await sb().auth.mfa.challengeAndVerify({ factorId, code: code.trim() });
    if (e) throw e;
    done();
  });

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Cloud className="h-5 w-5" /></div>
          <div><div className="font-semibold">Personal Cloud</div><div className="text-sm text-muted-foreground">Your servers, from anywhere</div></div>
        </div>
        {step === "loading" && <p className="text-sm text-muted-foreground">Loading…</p>}
        {(step === "signin" || step === "signup") && (
          <form onSubmit={step === "signin" ? signIn : signUp} className="space-y-4">
            <Field label="Email"><Input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus /></Field>
            <Field label="Password" hint={step === "signup" ? "At least 12 characters. You'll also set up an authenticator app." : undefined}>
              <Input type="password" autoComplete={step === "signin" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            <ErrorBox error={error} />
            <Button className="w-full" type="submit" loading={busy}>{step === "signin" ? "Sign in" : "Create account"}</Button>
            <button type="button" className="w-full text-center text-sm text-accent" onClick={() => { setError(null); setStep(step === "signin" ? "signup" : "signin"); }}>
              {step === "signin" ? "First time? Create your account" : "Already have an account? Sign in"}
            </button>
          </form>
        )}
        {step === "check-email" && <Notice tone="success">Check your inbox for the confirmation link, then come back and sign in.</Notice>}
        {step === "enroll" && enroll && (
          <form onSubmit={verifyCode} className="space-y-4">
            <div className="flex items-start gap-2 text-sm"><ShieldCheck className="mt-0.5 h-4 w-4 text-success" />Two-factor sign-in is required because this dashboard controls your computers.</div>
            <p className="text-sm text-muted-foreground">Scan with an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password…):</p>
            <img src={enroll.qr} alt="Authenticator QR code" className="mx-auto h-44 w-44 rounded-lg border bg-white p-1" />
            <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">Can't scan? Enter this key</summary><CopyBox className="mt-1" value={enroll.secret} /></details>
            <Field label="6-digit code"><Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} required /></Field>
            <ErrorBox error={error} />
            <Button className="w-full" type="submit" loading={busy}>Turn on and continue</Button>
          </form>
        )}
        {step === "verify" && (
          <form onSubmit={verifyCode} className="space-y-4">
            <Field label="Authenticator code" hint="Enter the 6-digit code from your authenticator app.">
              <Input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} required />
            </Field>
            <ErrorBox error={error} />
            <Button className="w-full" type="submit" loading={busy}>Verify</Button>
            <button type="button" className="w-full text-center text-sm text-muted-foreground" onClick={async () => { await sb().auth.signOut(); setStep("signin"); }}>Use another account</button>
          </form>
        )}
      </Card>
    </div>
  );
}
