# Punchwalk web app

The phone-first PWA (React + TypeScript + Vite + Tailwind v4). DESIGN.md at the repo root is the spec;
`docs/design/*.png` are the reviewed renders.

## Data: live or mock

The app talks to one interface (`src/api/api.ts`) with two adapters:

- **live** (the default): HTTP to the FastAPI app. In production the API serves the built app from
  `punchwalk/static/`, so it's same-origin. In `pnpm dev`, Vite proxies `/api`, `/media` and `/healthz`
  to `http://localhost:8000`.
- **mock**: answers from `fixtures/mock_api/` (see its README for the file naming). Used by `pnpm test`
  (always), `pnpm screenshots` (always), and offline development.

```sh
pnpm dev                    # live: start the API first (make dev, port 8000)
VITE_DATA=mock pnpm dev     # or: pnpm dev:mock, no backend needed
pnpm build                  # live build into ../punchwalk/static (no mock data in the bundle)
VITE_DATA=mock pnpm build   # mock build; copies fixtures to static/mock-api/ (demos without an API)
```

Mock-only URL switches (`?mock_scenario=first_run`, `?mock_progress=5`, …) are listed in
`screenshots/README.md`.

## Checks

```sh
pnpm typecheck   # app + node scripts
pnpm test        # vitest (mock data)
pnpm build
pnpm screenshots # Playwright WebKit, iPhone 15 Pro at 393 x 852 -> ../.verify/screens/ (see screenshots/README.md)
```

## Where things are

- `src/styles/index.css`: design tokens (the only place colors, type, radii and the shadow are defined)
- `src/components/`: shared components (tape tab, buttons, chips, pills, sheets, bars, hand overlay, …);
  `/dev/components` shows all of them
- `src/features/<screen>/`: screen logic, mostly pure functions with tests next to them
- `src/pages/`: one file per route (`src/router.tsx` lists them)
- `scripts/gen-icons.ts`: regenerates the PWA icons from one SVG (`pnpm icons`)
