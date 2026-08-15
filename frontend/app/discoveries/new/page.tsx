"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function parseList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

const inputClass =
  "mt-1 w-full rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-900";

export default function NewDiscoveryPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  const [leadType, setLeadType] = useState("");
  const [location, setLocation] = useState("");
  const [industry, setIndustry] = useState("");
  const [sizeMin, setSizeMin] = useState("");
  const [sizeMax, setSizeMax] = useState("");
  const [roles, setRoles] = useState("");
  const [keywords, setKeywords] = useState("");
  const [exclude, setExclude] = useState("");
  const [numLeads, setNumLeads] = useState("10");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createDiscovery({
        lead_type: leadType,
        location,
        industry: industry || undefined,
        company_size_min: sizeMin ? Number(sizeMin) : undefined,
        company_size_max: sizeMax ? Number(sizeMax) : undefined,
        target_roles: parseList(roles),
        keywords: parseList(keywords),
        exclude_keywords: parseList(exclude),
        num_leads: numLeads ? Number(numLeads) : 10,
      });
      router.push(`/discoveries/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create discovery");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex-1 p-6">
      <form
        onSubmit={onSubmit}
        className="mx-auto w-full max-w-2xl rounded-xl border border-zinc-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold tracking-tight">New discovery</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Define your ideal customer profile.
        </p>

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <label className="text-sm font-medium">
            Lead type / business category <span className="text-red-500">*</span>
            <input
              className={inputClass}
              value={leadType}
              onChange={(e) => setLeadType(e.target.value)}
              placeholder="e.g. Dental Clinics"
              required
            />
          </label>

          <label className="text-sm font-medium">
            Location <span className="text-red-500">*</span>
            <input
              className={inputClass}
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="e.g. Bengaluru, India"
              required
            />
          </label>

          <label className="text-sm font-medium">
            Industry
            <input
              className={inputClass}
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              placeholder="e.g. Healthcare"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm font-medium">
              Company size (min)
              <input
                className={inputClass}
                type="number"
                min={1}
                value={sizeMin}
                onChange={(e) => setSizeMin(e.target.value)}
                placeholder="e.g. 10"
              />
            </label>
            <label className="text-sm font-medium">
              Company size (max)
              <input
                className={inputClass}
                type="number"
                min={1}
                value={sizeMax}
                onChange={(e) => setSizeMax(e.target.value)}
                placeholder="e.g. 200"
              />
            </label>
          </div>

          <label className="text-sm font-medium sm:col-span-2">
            Target roles <span className="text-zinc-400">(comma-separated)</span>
            <input
              className={inputClass}
              value={roles}
              onChange={(e) => setRoles(e.target.value)}
              placeholder="e.g. Owner, Practice Manager"
            />
          </label>

          <label className="text-sm font-medium">
            Keywords <span className="text-zinc-400">(comma-separated)</span>
            <input
              className={inputClass}
              value={keywords}
              onChange={(e) => setKeywords(e.target.value)}
              placeholder="e.g. implant, orthodontist"
            />
          </label>

          <label className="text-sm font-medium">
            Exclude keywords <span className="text-zinc-400">(comma-separated)</span>
            <input
              className={inputClass}
              value={exclude}
              onChange={(e) => setExclude(e.target.value)}
              placeholder="e.g. franchise, mobile"
            />
          </label>

          <label className="text-sm font-medium">
            Number of leads <span className="text-red-500">*</span>
            <input
              className={inputClass}
              type="number"
              min={1}
              value={numLeads}
              onChange={(e) => setNumLeads(e.target.value)}
              required
            />
          </label>
        </div>

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        <div className="mt-6 flex items-center gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50"
          >
            {busy ? "Creating…" : "Create & run"}
          </button>
          <Link
            href="/"
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm transition-colors hover:bg-zinc-50"
          >
            Cancel
          </Link>
        </div>
      </form>
    </main>
  );
}
