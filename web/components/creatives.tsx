"use client";

import { useEffect, useState } from "react";
import { api, apiBlobUrl, ApiError, useApi } from "@/lib/api";
import { label } from "@/lib/session";
import { Badge, Button, ErrorState, Loading, Notice } from "@/components/ui";

type Check = { check: string; caps: string; message: string };
type Fix = { target: string; problem: string; instruction: string };
type SizeView = {
  size: string;
  ratio: string;
  url: string;
  scores: Record<string, number>;
  fixes: Fix[];
  checks: Check[];
  passed: boolean;
  ai_generated: boolean;
  alt_text: string;
};
type Creatives = {
  rounds: number;
  passed?: boolean;
  stale?: boolean;
  sizes: SizeView[];
  history: { round: number; passed: boolean; lowest: number }[];
  job: { status: string; detail: string };
};

function ProtectedImage({ path, alt }: { path: string; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    let live = true;
    apiBlobUrl(path)
      .then((made) => {
        url = made;
        if (live) setSrc(made);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [path]);
  if (failed) return <p className="text-sm text-bad">The image could not be loaded.</p>;
  if (!src) return <div className="aspect-square animate-pulse rounded-lg bg-raised" />;
  return <img src={src} alt={alt} className="w-full rounded-lg border border-line" />;
}

function SizeCard({ view }: { view: SizeView }) {
  const low = Object.entries(view.scores).filter(([, score]) => score < 8);
  return (
    <figure className="min-w-0">
      <ProtectedImage path={view.url} alt={view.alt_text || `Ad image, ${view.ratio}`} />
      <figcaption className="mt-2 space-y-1.5 text-sm">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold tabular-nums">{view.ratio}</span>
          <Badge tone={view.passed ? "good" : "warn"}>{view.passed ? "Passed" : "Needs work"}</Badge>
          {view.ai_generated && <Badge tone="accent">AI-generated image</Badge>}
        </div>
        <ul className="grid grid-cols-2 gap-x-3 text-xs tabular-nums text-muted">
          {Object.entries(view.scores).map(([name, score]) => (
            <li key={name} className={`flex justify-between ${score < 8 ? "font-semibold text-bad" : ""}`}>
              <span>{label(name)}</span>
              <span>{score}</span>
            </li>
          ))}
        </ul>
        {(view.checks.length > 0 || view.fixes.length > 0 || low.length > 0) && (
          <ul className="space-y-1 text-xs">
            {view.checks.map((check) => (
              <li key={check.message} className="text-bad">
                Measured: {check.message}
              </li>
            ))}
            {view.fixes.map((fix, index) => (
              <li key={index}>
                <span className="font-medium">{label(fix.target)}:</span> {fix.problem}. {fix.instruction}
              </li>
            ))}
          </ul>
        )}
      </figcaption>
    </figure>
  );
}

export function AdImages({ draftId, disabled }: { draftId: number; disabled: boolean }) {
  const [poll, setPoll] = useState<number | undefined>(undefined);
  const creatives = useApi<Creatives>(`/drafts/${draftId}/creatives`, poll);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const running = creatives.data?.job.status === "running";
  useEffect(() => setPoll(running ? 3000 : undefined), [running]);

  async function make() {
    setBusy(true);
    setError(null);
    try {
      await api(`/drafts/${draftId}/creatives`, { method: "POST" });
      setPoll(3000);
      creatives.reload();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (creatives.loading && !creatives.data) return <Loading rows={1} />;
  if (creatives.error) return <ErrorState error={creatives.error} retry={creatives.reload} />;
  const data = creatives.data!;
  return (
    <section aria-label="Ad images">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase text-muted">Ad images</p>
        {!disabled && (
          <Button busy={busy || running} onClick={make}>
            {data.rounds ? "Make again" : "Make ad images"}
          </Button>
        )}
      </div>
      {error && <ErrorState error={error} />}
      {running && <Notice>Rendering three sizes and asking the vision critic. This takes a minute or two.</Notice>}
      {data.job.status === "failed" && <Notice tone="bad">{data.job.detail}</Notice>}
      {data.rounds === 0 && !running ? (
        <p className="text-sm text-muted">No images yet. They are rendered from the brand kit, in 1:1, 4:5 and 9:16, and reviewed before you see them.</p>
      ) : (
        data.rounds > 0 && (
          <>
            {data.stale && <Notice tone="warn">These images were made from an earlier version of the text. Make them again to match your edits.</Notice>}
            <p className="mb-2 text-sm text-muted">
              {data.passed ? "Passed" : "Did not pass"} the vision critic after {data.rounds} round{data.rounds === 1 ? "" : "s"}
              {data.history.length > 1 && ` (lowest score by round: ${data.history.map((h) => h.lowest).join(", ")})`}.
            </p>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              {data.sizes.map((view) => (
                <SizeCard key={view.size} view={view} />
              ))}
            </div>
          </>
        )
      )}
    </section>
  );
}

export function LandingPreview({ draftId }: { draftId: number }) {
  const page = useApi<string>(`/drafts/${draftId}/landing.html`);
  if (page.loading && !page.data) return <Loading rows={1} />;
  if (page.error) return <ErrorState error={page.error} retry={page.reload} />;
  return (
    <section aria-label="Landing page preview">
      <p className="mb-2 text-xs font-semibold uppercase text-muted">As a page</p>
      <iframe
        title="Landing page preview"
        sandbox=""
        srcDoc={page.data}
        className="h-[28rem] w-full rounded-lg border border-line bg-white"
      />
    </section>
  );
}
