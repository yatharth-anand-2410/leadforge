"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { api, type Job, type JobStatus, type Lead } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-zinc-100 text-zinc-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

type Filter = "shortlisted" | "";

export default function DiscoveryDetailPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = Number(params.id);

  const [filter, setFilter] = useState<Filter>("shortlisted");
  const [rerunning, setRerunning] = useState(false);

  const { data: discovery } = useSWR(id ? `discovery-${id}` : null, () =>
    api.getDiscovery(id),
  );
  const { data: jobs, mutate: mutateJobs } = useSWR(
    id ? `jobs-${id}` : null,
    () => api.listJobs(id),
    { refreshInterval: 2000 },
  );
  const { data: leads, mutate: mutateLeads } = useSWR(
    id ? `leads-${id}-${filter}` : null,
    () => api.listLeads(id, filter || undefined),
    { refreshInterval: 3000 },
  );

  const latest: Job | undefined = jobs?.[0];

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

  async function reRun() {
    setRerunning(true);
    try {
      await api.runDiscovery(id);
      mutateJobs();
      mutateLeads();
    } catch {
      /* surfaced by SWR on next poll */
    } finally {
      setRerunning(false);
    }
  }

  const leadCount = typeof latest?.progress?.lead_count === "number" ? latest.progress.lead_count : null;

  return (
    <main className="flex-1 p-6">
      <div className="mx-auto max-w-5xl">
        <Link href="/" className="text-sm text-zinc-500 hover:underline">
          ← Back
        </Link>

        <header className="mt-3 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {discovery?.name ?? "Discovery"}
            </h1>
            <p className="mt-1 text-sm text-zinc-500">
              {discovery?.lead_type} · {discovery?.location} · target{" "}
              {discovery?.num_leads} leads
            </p>
          </div>
          <button
            onClick={reRun}
            disabled={rerunning || latest?.status === "running"}
            className="shrink-0 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50"
          >
            {rerunning ? "Queuing…" : "Re-run"}
          </button>
        </header>

        {latest && (
          <div className="mt-5 flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 bg-white p-4">
            <span
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                STATUS_STYLES[latest.status] ?? "bg-zinc-100 text-zinc-700"
              }`}
            >
              {latest.status}
            </span>
            {leadCount !== null && (
              <span className="text-sm text-zinc-600">
                {leadCount} shortlisted lead{leadCount === 1 ? "" : "s"}
              </span>
            )}
            {latest.error && (
              <span className="text-sm text-red-600">{latest.error}</span>
            )}
          </div>
        )}

        <div className="mt-6 flex items-center justify-between">
          <div className="inline-flex rounded-md border border-zinc-300 p-0.5">
            {(["shortlisted", ""] as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
                  filter === f ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100"
                }`}
              >
                {f === "shortlisted" ? "Shortlisted" : "All"}
              </button>
            ))}
          </div>
          <span className="text-sm text-zinc-500">
            {leads?.length ?? 0} lead{leads?.length === 1 ? "" : "s"}
          </span>
        </div>

        <div className="mt-3 overflow-x-auto rounded-xl border border-zinc-200 bg-white">
          <table className="w-full min-w-[980px] text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-left text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Website</th>
                <th className="px-4 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium">Phone</th>
                <th className="px-4 py-3 font-medium">Score</th>
                <th className="px-4 py-3 font-medium">Why it fits</th>
                <th className="px-4 py-3 font-medium">Verified</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100">
              {!leads || leads.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-zinc-400">
                    {latest?.status === "running"
                      ? "Discovering leads…"
                      : "No leads yet."}
                  </td>
                </tr>
              ) : (
                leads.map((lead) => (
                  <LeadRow key={lead.id} lead={lead} />
                ))
              )}
            </tbody>
          </table>
        </div>

        <p className="mt-6 text-xs text-zinc-400">
          Business data © OpenStreetMap contributors.
        </p>
      </div>
    </main>
  );
}

function LeadRow({ lead }: { lead: Lead }) {
  return (
    <tr className="align-top hover:bg-zinc-50">
      <td className="px-4 py-3">
        <div className="font-medium text-zinc-900">{lead.name}</div>
        {lead.contact_name && (
          <div className="text-xs text-zinc-500">{lead.contact_name}</div>
        )}
      </td>
      <td className="px-4 py-3 text-zinc-600">
        {[lead.city, lead.country].filter(Boolean).join(", ") || "—"}
      </td>
      <td className="px-4 py-3">
        {lead.website ? (
          <a
            href={lead.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            {lead.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
          </a>
        ) : (
          "—"
        )}
      </td>
      <td className="px-4 py-3 text-zinc-600">{lead.email ?? "—"}</td>
      <td className="px-4 py-3 text-zinc-600">{lead.phone ?? "—"}</td>
      <td className="px-4 py-3 font-medium text-zinc-900">
        {Math.round(lead.score)}
      </td>
      <td className="px-4 py-3 max-w-sm">
        {lead.fit_reason ? (
          <div>
            <div className="text-zinc-600">{lead.fit_reason}</div>
            {typeof lead.fit_score === "number" && (
              <div className="mt-0.5 text-xs text-zinc-400">
                ICP fit {lead.fit_score}/50
              </div>
            )}
          </div>
        ) : (
          "—"
        )}
      </td>
      <td className="px-4 py-3">
        {lead.verified ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
            ✓ Verified
          </span>
        ) : (
          <span className="text-xs text-zinc-400">—</span>
        )}
      </td>
    </tr>
  );
}
