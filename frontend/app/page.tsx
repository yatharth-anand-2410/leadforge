"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Discovery, type JobStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STATUS_STYLES: Record<JobStatus, string> = {
  queued: "bg-zinc-100 text-zinc-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

export default function Dashboard() {
  const { user, loading, logout } = useAuth();
  const router = useRouter();
  const [discoveries, setDiscoveries] = useState<Discovery[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    api
      .listDiscoveries()
      .then(setDiscoveries)
      .catch((e: Error) => setError(e.message));
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <main className="flex flex-1 items-center justify-center text-zinc-400">
        Loading…
      </main>
    );
  }

  async function deleteDiscovery(id: number) {
    setDeleting(true);
    try {
      await api.deleteDiscovery(id);
      setDiscoveries((prev) => (prev ? prev.filter((d) => d.id !== id) : prev));
      setConfirmingId(null);
    } catch (e: Error | unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete discovery");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <main className="flex-1 p-6">
      <header className="mx-auto flex max-w-4xl items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Discoveries</h1>
          <p className="text-sm text-zinc-500">Signed in as {user.username}</p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/discoveries/new"
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700"
          >
            New discovery
          </Link>
          <button
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm transition-colors hover:bg-zinc-50"
          >
            Sign out
          </button>
        </div>
      </header>

      {error && (
        <p className="mx-auto mt-6 max-w-4xl text-sm text-red-600">{error}</p>
      )}

      {discoveries === null ? (
        <p className="mx-auto mt-6 max-w-4xl text-sm text-zinc-400">
          Loading discoveries…
        </p>
      ) : discoveries.length === 0 ? (
        <div className="mx-auto mt-10 max-w-4xl rounded-xl border border-dashed border-zinc-300 p-10 text-center">
          <p className="text-zinc-500">No discoveries yet.</p>
          <Link
            href="/discoveries/new"
            className="mt-2 inline-block text-sm font-medium text-zinc-900 underline"
          >
            Create your first discovery
          </Link>
        </div>
      ) : (
        <ul className="mx-auto mt-6 max-w-4xl divide-y divide-zinc-200 rounded-xl border border-zinc-200 bg-white">
          {discoveries.map((d) => (
            <li
              key={d.id}
              className="flex items-center justify-between gap-4 p-4"
            >
              <div className="min-w-0">
                <Link
                  href={`/discoveries/${d.id}`}
                  className="truncate font-medium text-zinc-900 hover:underline"
                >
                  {d.name}
                </Link>
                <p className="truncate text-sm text-zinc-500">
                  {d.lead_type} · {d.location} · {d.num_leads} leads
                </p>
              </div>
              {d.latest_job ? (
                <span
                  className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${
                    STATUS_STYLES[d.latest_job.status] ??
                    "bg-zinc-100 text-zinc-700"
                  }`}
                >
                  {d.latest_job.status}
                </span>
              ) : null}
              {confirmingId === d.id ? (
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    onClick={() => deleteDiscovery(d.id)}
                    disabled={deleting}
                    className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
                  >
                    {deleting ? "Deleting…" : "Confirm"}
                  </button>
                  <button
                    onClick={() => setConfirmingId(null)}
                    disabled={deleting}
                    className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs transition-colors hover:bg-zinc-50 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmingId(d.id)}
                  className="shrink-0 text-sm text-red-600 transition-colors hover:text-red-700"
                >
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
