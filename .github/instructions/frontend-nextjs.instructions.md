---
description: "Use when working on llmxy website or admin Next.js apps, Tailwind UI, dashboard pages, login flows, API wrappers, SWR data fetching, or TypeScript React components."
applyTo:
  - "website/src/**/*"
  - "admin/src/**/*"
  - "website/package.json"
  - "admin/package.json"
  - "website/tailwind.config.ts"
  - "admin/tailwind.config.ts"
---
# Frontend Next.js Instructions

## App Boundaries

- `website/` is the customer portal: registration, login, OAuth entry points, plan purchase, API key management, usage, and billing.
- `admin/` is the operator console: users, usage, plans, channels, models, routes, and Envoy instances.
- Both apps call only the FastAPI backend. Do not add a second backend or duplicate backend business logic in Next.js routes.

## API Access

- Use each app's `src/lib/api.ts` helper for backend calls. It sets `Content-Type`, bearer auth, 401 behavior, and parses backend errors.
- `NEXT_PUBLIC_API_BASE_URL` defaults to `http://localhost:8000` and is the only frontend backend base URL knob.
- Website JWT token key is `llmxy_token`; admin JWT token key is `llmxy_admin_token`.
- For unauthenticated calls, pass `skipAuth: true` instead of manually omitting headers.
- Use the exported `fetcher` with SWR when the surrounding code already uses SWR.

## React And TypeScript

- These are Next.js 14 App Router apps with strict TypeScript. Keep browser-only APIs such as `localStorage` inside client components or guarded helper functions.
- Reuse local components before creating new ones: `src/components/ui.tsx` in both apps, plus website-specific components such as `HeaderNav` and `HeroCta`.
- Keep shared types close to the API-consuming page or helper unless a type is reused in multiple places.
- Preserve each app's existing path alias: `@/*` maps to `src/*`.

## UI Direction

- Admin UI should feel like an operational tool: compact layouts, clear tables/forms, predictable filters and actions, restrained colors, and no marketing-style hero sections.
- Website UI can be more customer-facing, but should still use the existing Tailwind brand palette and components.
- Prefer explicit loading, empty, error, disabled, and pending states for data-changing actions.
- Do not expose implementation instructions or keyboard-shortcut prose in the visible UI unless the product surface already does so.

## Package And Validation

- This repo currently has npm lockfiles. Use `npm install`, `npm ci`, `npm run dev`, and `npm run build` unless the package manager is intentionally changed.
- Validate TypeScript with `npx tsc --noEmit --incremental false` from the relevant app directory.
- `npm run lint` may trigger Next.js first-run ESLint setup in this repo. Prefer TypeScript checks unless ESLint setup has been completed.
