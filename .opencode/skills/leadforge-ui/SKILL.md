---
name: leadforge-ui
description: Use when building or changing any UI in the Lead Forge repo (frontend/ pages, components, Tailwind classes). Codifies the design language and conventions so new and polished UI stays consistent with the current dashboard, discovery, and auth pages.
---

# Lead Forge UI

Static design language for the Lead Forge Next.js frontend. Follow it for any
change under `frontend/` so screens stay visually consistent.

## Stack

- Next.js App Router, TypeScript, `"use client"` on interactive pages.
- Tailwind CSS v4 (single `@import "tailwindcss"` in `app/globals.css`). Do not
  add a config file; use `@theme inline` for tokens.
- Fonts: Geist Sans (`--font-geist-sans`) and Geist Mono (`--font-geist-mono`)
  via `next/font/google`, attached through `--font-sans` / `--font-mono`.
- No component library — plain Tailwind classes only.

## Palette

- Base: `zinc` scale on white (`--background: #ffffff`).
- Ink / primary actions: `bg-zinc-900`, hover `bg-zinc-700`, always white text.
- Secondary buttons / borders: `border-zinc-300`, hover `bg-zinc-50`.
- Muted text: `text-zinc-500`, lighter `text-zinc-400`, heading ink `text-zinc-900`.
- Faint dividers: `divide-zinc-200`, `border-zinc-200`, inner `divide-zinc-100`.
- Status pills: `queued` -> `bg-zinc-100 text-zinc-700`, `running` -> `bg-blue-100 text-blue-700`,
  `completed` -> `bg-green-100 text-green-700`, `failed` -> `bg-red-100 text-red-700`.
- Semantic accents: links/danger `text-red-600`, verified badge `bg-green-100 text-green-700`,
  hyperlinks `text-blue-600 hover:underline`.
- Keep it monochrome-first: zinc for chrome, color reserved for status and verified signals.

## Components / patterns

- Page shell: `<main className="flex-1 p-6">` with an inner `mx-auto max-w-4xl` (dashboard)
  or `max-w-5xl` (detail) container.
- Card: `rounded-xl border border-zinc-200 bg-white shadow-sm`.
- Primary button: `rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50`.
- Secondary button: `rounded-md border border-zinc-300 px-4 py-2 text-sm transition-colors hover:bg-zinc-50`.
- Input: `mt-1 w-full rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-900`.
- Status pill: `rounded-full px-2.5 py-1 text-xs font-medium` + the status color above.
- Back link: `text-sm text-zinc-500 hover:underline` with a leading `←`.
- Empty state: `rounded-xl border border-dashed border-zinc-300 p-10 text-center`.
- Loading state: centered `text-zinc-400` with `Loading…`.

## Layout details

- Headings: `text-2xl font-semibold tracking-tight` (page) / `text-xl` (card title);
  section caption `text-sm text-zinc-500`.
- Cards and tables sit on white `bg-white` inside the page's zinc background.
- Segmented filter control: `inline-flex rounded-md border border-zinc-300 p-0.5`,
  active tab `bg-zinc-900 text-white`, inactive `text-zinc-600 hover:bg-zinc-100`.
- Tables: `w-full min-w-… text-sm`, header `text-left text-xs uppercase tracking-wide text-zinc-500`,
  rows `divide-y divide-zinc-100`, hover `hover:bg-zinc-50`.
- Responsive: prefer `grid gap-5 sm:grid-cols-2` for multi-column forms.

## When changing existing pages

- Reuse the exact classes above; do not invent new chromes or accent colors.
- Prefer editing existing files — only add components when extraction is clearly warranted.
- Keep attribution line `Business data © OpenStreetMap contributors.` (this project sources
  OSM data).