"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Message =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string };

const EXAMPLES = [
  "Dental clinics in Bengaluru with a marketing manager, 5–50 staff. We sell local-SEO and review-reputation packages.",
  "SMEs in the food and hospitality space across Phoenix, AZ that need social media management.",
  "Boutique gyms and fitness studios in Dubai looking for a lead-gen and WhatsApp chat automation service.",
];

const WELCOME: Message = {
  role: "assistant",
  text: "Describe your ideal customer in plain English — who they are, where they are, and what to target. Set the number of leads you want below. This becomes the discovery agent's instruction; there's no form to fill in.",
};

export default function NewDiscoveryPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [draft, setDraft] = useState("");
  const [numLeads, setNumLeads] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useAutofocus();

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <main className="flex flex-1 items-center justify-center text-zinc-400">
        Loading…
      </main>
    );
  }

  async function submit(description: string) {
    const text = description.trim();
    if (!text || busy) return;

    setMessages((prev) => [...prev, { role: "user", text }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const parsed = numLeads ? parseInt(numLeads, 10) : undefined;
      const created = await api.createDiscovery({
        brief: text,
        ...(parsed && parsed > 0 ? { num_leads: parsed } : {}),
      });
      router.push(`/discoveries/${created.id}`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create discovery",
      );
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    submit(draft);
  }

  return (
    <main className="flex-1 p-6">
      <div className="mx-auto w-full max-w-3xl">
        <Link href="/" className="text-sm text-zinc-500 hover:underline">
          ← Back
        </Link>

        <div className="mt-3 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              New discovery
            </h1>
            <p className="mt-1 text-sm text-zinc-500">
              Tell the agent who to find — it will do the rest.
            </p>
          </div>
        </div>

        <div className="mt-5 flex h-[420px] flex-col rounded-xl border border-zinc-200 bg-white shadow-sm">
          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-zinc-900 px-4 py-2.5 text-sm text-white">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="flex justify-start">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm text-zinc-700">
                    {m.text}
                  </div>
                </div>
              ),
            )}
            {busy && (
              <div className="flex justify-start">
                <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm text-zinc-400">
                  Kicking off discovery…
                </div>
              </div>
            )}
          </div>

          <form
            onSubmit={onSubmit}
            className="border-t border-zinc-200 p-3"
          >
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit(draft);
                }
              }}
              rows={3}
              placeholder="Describe your ICP in plain English…"
              className="w-full resize-none rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-900"
            />
            <div className="mt-2 flex items-center justify-between gap-4">
              <label className="flex items-center gap-2 text-sm text-zinc-600">
                How many leads?
                <input
                  type="number"
                  min={1}
                  value={numLeads}
                  onChange={(e) => setNumLeads(e.target.value)}
                  placeholder="10"
                  className="w-20 rounded-md border border-zinc-300 px-2 py-1 text-sm outline-none focus:border-zinc-900"
                />
              </label>
              <div className="flex items-center gap-3">
                {error && <p className="text-xs text-red-600">{error}</p>}
                {!busy && !messages.some((m) => m.role === "user") && (
                  <Labels examples={EXAMPLES} onPick={(t) => submit(t)} />
                )}
                <button
                  type="submit"
                  disabled={busy || !draft.trim()}
                  className="shrink-0 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50"
                >
                  {busy ? "Starting…" : "Start discovery"}
                </button>
              </div>
            </div>
          </form>
        </div>

        <p className="mt-4 text-xs text-zinc-400">
          Your description is used verbatim as the discovery agent instructions —
          only the lead count is a separate setting. Leave it blank for the
          default of 10.
        </p>
      </div>
    </main>
  );
}

function useAutofocus() {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return ref;
}

function Labels({
  examples,
  onPick,
}: {
  examples: string[];
  onPick: (t: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      {examples.map((ex) => {
        const short = ex.split(",")[0];
        return (
          <button
            key={ex}
            type="button"
            onClick={() => onPick(ex)}
            className="hidden rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 sm:inline-block"
            title={ex}
          >
            {short}
          </button>
        );
      })}
    </div>
  );
}