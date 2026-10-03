"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, useSession } from "@/lib/session";
import { Badge, Button, Card, ErrorState, Field, inputClass, Loading, Notice, PageHeader } from "@/components/ui";

type Meta = { status: "inferred" | "confirmed"; confidence: string; note: string; source_urls: string[] };
type BrainPayload = {
  brain: Record<string, unknown> & { fields: Record<string, Meta>; version: number; business: { name: string } };
  field_paths: string[];
  weakest: { path: string; reason: string }[];
};

const GROUPS: [string, string][] = [
  ["business", "The business"],
  ["icp", "Ideal customer"],
  ["voice", "Brand voice"],
  ["products", "Products"],
  ["proof", "Proof"],
  ["competitors", "Competitors"],
];
const STEPS = [
  ["crawling", "Reading the website", "Up to 20 pages, respecting robots.txt"],
  ["drafting", "Drafting the brain", "Business, customer, voice, products and proof"],
  ["done", "Ready for your review", "Every field is marked inferred until you confirm it"],
];

const read = (source: unknown, path: string) =>
  path.split(".").reduce<unknown>((node, key) => (node as Record<string, unknown>)?.[key], source);

function Value({ value }: { value: unknown }) {
  if (value === "" || value === "unknown" || (Array.isArray(value) && !value.length))
    return <p className="text-sm italic text-muted">Not found on the website</p>;
  if (typeof value === "string") return <p className="whitespace-pre-wrap text-sm">{value}</p>;
  if (Array.isArray(value))
    return (
      <ul className="list-disc space-y-1 pl-5 text-sm">
        {value.map((item, index) => (
          <li key={index}>
            {typeof item === "string"
              ? item
              : Object.values(item as object).filter((part) => typeof part === "string" && part).join(" · ")}
          </li>
        ))}
      </ul>
    );
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {Object.entries(value as object).map(([key, part]) => (
        <div key={key} className="contents">
          <dt className="text-muted">{label(key)}</dt>
          <dd>{Array.isArray(part) ? part.join("; ") || "none" : String(part)}</dd>
        </div>
      ))}
    </dl>
  );
}

function FieldRow(props: { path: string; data: BrainPayload; onChange: (data: BrainPayload) => void; workspace: string }) {
  const { path, data, workspace } = props;
  const value = read(data.brain, path);
  const meta = data.brain.fields[path];
  const simpleList = Array.isArray(value) && value.every((item) => typeof item === "string");
  const editable = typeof value === "string" || simpleList || (Array.isArray(value) && value.length === 0 && !path.match(/products|proof|competitors/));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function send(path_: string, body: unknown, method = "POST") {
    setBusy(true);
    setError(null);
    try {
      props.onChange(await api<BrainPayload>(path_, { method, body }));
      setEditing(false);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-b border-line py-4 last:border-0">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{label(path.split(".").slice(1).join(" ") || path)}</span>
        {meta?.status === "confirmed" ? (
          <Badge tone="good">Confirmed by you</Badge>
        ) : (
          <Badge tone="warn">Inferred · {meta?.confidence ?? "low"} confidence</Badge>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea className={inputClass} rows={typeof value === "string" ? 3 : 5} value={draft} onChange={(event) => setDraft(event.target.value)} aria-label={`Edit ${path}`} />
          {typeof value !== "string" && <p className="text-xs text-muted">One item per line.</p>}
          <div className="flex gap-2">
            <Button variant="primary" busy={busy} onClick={() => send(`/workspaces/${workspace}/brain/field`, { path, value: typeof value === "string" ? draft : draft.split("\n").map((line) => line.trim()).filter(Boolean) }, "PUT")}>
              Save and confirm
            </Button>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <>
          <Value value={value} />
          {meta?.status !== "confirmed" && meta?.note && <p className="mt-2 text-xs text-muted">Why it is uncertain: {meta.note}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            {meta?.status !== "confirmed" && (
              <Button busy={busy} onClick={() => send(`/workspaces/${workspace}/brain/confirm`, { paths: [path] })}>
                This is right
              </Button>
            )}
            {editable && (
              <Button variant="ghost" onClick={() => { setDraft(typeof value === "string" ? (value === "unknown" ? "" : value) : (value as string[]).join("\n")); setEditing(true); }}>
                Edit
              </Button>
            )}
          </div>
        </>
      )}
      {error && <div className="mt-2"><ErrorState error={error} /></div>}
    </div>
  );
}

function Review({ workspace }: { workspace: string }) {
  const brain = useApi<BrainPayload>(`/workspaces/${workspace}/brain`);
  const [override, setOverride] = useState<BrainPayload>();
  const [onlyUnconfirmed, setOnlyUnconfirmed] = useState(true);
  if (brain.loading) return <Loading rows={4} />;
  if (brain.error) return <ErrorState error={brain.error} retry={brain.reload} />;
  const data = override ?? brain.data!;
  const confirmed = data.field_paths.filter((path) => data.brain.fields[path]?.status === "confirmed").length;
  const total = data.field_paths.length;
  const weakest = new Set(data.weakest.map((item) => item.path));
  return (
    <>
      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-medium">{confirmed} of {total} fields confirmed</p>
            <p className="mt-0.5 text-sm text-muted">The team treats anything you have not confirmed as an assumption.</p>
          </div>
          <label className="flex min-h-10 items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4" checked={onlyUnconfirmed} onChange={(event) => setOnlyUnconfirmed(event.target.checked)} />
            Only show what needs me
          </label>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-raised">
          <div className="h-full rounded-full bg-good" style={{ width: `${(confirmed / total) * 100}%` }} />
        </div>
      </Card>
      {confirmed === total && onlyUnconfirmed && <Notice tone="good">Everything is confirmed. Untick the filter to see or edit any field.</Notice>}
      {GROUPS.map(([prefix, title]) => {
        const paths = data.field_paths
          .filter((path) => path.split(".")[0] === prefix)
          .filter((path) => !onlyUnconfirmed || data.brain.fields[path]?.status !== "confirmed")
          .sort((a, b) => Number(weakest.has(b)) - Number(weakest.has(a)));
        if (!paths.length) return null;
        return (
          <Card key={prefix} className="mb-4">
            <h2 className="text-base font-semibold">{title}</h2>
            {paths.map((path) => <FieldRow key={path} path={path} data={data} onChange={setOverride} workspace={workspace} />)}
          </Card>
        );
      })}
    </>
  );
}

function Progress({ workspace, onDone }: { workspace: string; onDone: () => void }) {
  const job = useApi<{ status: string; detail: string }>(`/workspaces/${workspace}/onboarding`, 2000);
  const status = job.data?.status ?? "crawling";
  if (status === "done") onDone();
  const reached = STEPS.findIndex(([key]) => key === status);
  return (
    <Card>
      {status === "failed" ? (
        <ErrorState error={new Error(job.data?.detail || "Onboarding failed")} />
      ) : (
        <ol className="space-y-4" aria-live="polite">
          {STEPS.map(([key, title, hint], index) => (
            <li key={key} className="flex gap-3">
              <span className={`mt-1 h-3 w-3 shrink-0 rounded-full ${index < reached ? "bg-good" : index === reached ? "animate-pulse bg-accent" : "border border-line"}`} />
              <div>
                <p className={`text-sm font-medium ${index > reached ? "text-muted" : ""}`}>{title}</p>
                <p className="text-sm text-muted">{index === reached && job.data?.detail ? job.data.detail : hint}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Onboarding() {
  const { workspace, workspaces, setWorkspace, reloadWorkspaces } = useSession();
  const router = useRouter();
  const wantsNew = useSearchParams().get("new") === "1" || workspaces.length === 0;
  const current = workspaces.find((item) => item.workspace === workspace);
  const [building, setBuilding] = useState<string | null>(current && !["done", "none"].includes(current.onboarding) ? workspace : null);
  const [url, setUrl] = useState("");
  const [answers, setAnswers] = useState({ what_they_sell: "", pricing: "", geography: "", ideal_customer: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ workspace: string }>("/onboarding", { method: "POST", body: { url, answers } });
      reloadWorkspaces();
      setWorkspace(result.workspace);
      setBuilding(result.workspace);
      router.replace("/onboarding");
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (building)
    return (
      <>
        <PageHeader title="Building the brain" subtitle="This usually takes a few minutes. You can leave this page." />
        <Progress workspace={building} onDone={() => { setBuilding(null); reloadWorkspaces(); }} />
      </>
    );

  if (wantsNew)
    return (
      <>
        <PageHeader title="Add a business" subtitle="Give us the website. We read it and draft what the team needs to know." />
        <form onSubmit={submit} className="max-w-xl space-y-4">
          <Card className="space-y-4">
            <Field label="Website address">
              <input className={inputClass} type="url" required placeholder="https://example.com" value={url} onChange={(event) => setUrl(event.target.value)} />
            </Field>
            <details>
              <summary className="cursor-pointer text-sm font-medium text-accent">Answer four quick questions (optional, but they count as confirmed)</summary>
              <div className="mt-4 space-y-4">
                {([["what_they_sell", "What do you sell, in one sentence?"], ["pricing", "How is it priced?"], ["geography", "Where do you sell?"], ["ideal_customer", "Who is your ideal customer?"]] as const).map(([key, question]) => (
                  <Field key={key} label={question}>
                    <input className={inputClass} value={answers[key]} onChange={(event) => setAnswers({ ...answers, [key]: event.target.value })} />
                  </Field>
                ))}
              </div>
            </details>
            {error && <ErrorState error={error} />}
            <div className="flex gap-2">
              <Button variant="primary" type="submit" busy={busy}>Build the brain</Button>
              {workspaces.length > 0 && <Button type="button" onClick={() => router.replace("/onboarding")}>Cancel</Button>}
            </div>
          </Card>
        </form>
      </>
    );

  return (
    <>
      <PageHeader
        title={`What we know about ${current?.name ?? workspace}`}
        subtitle="Drafted from the website. Confirm what is right and fix what is not."
        actions={<Button onClick={() => router.push("/onboarding?new=1")}>Add a business</Button>}
      />
      <Review key={workspace} workspace={workspace} />
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <Onboarding />
    </Suspense>
  );
}
