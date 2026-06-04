---
name: substrateos-deploy
description: >-
  Build and deploy the SubStrateOS apps — the substrateos-api backend and/or the
  substrateos-web frontend — to Azure Container Apps (centralindia). Use this
  whenever the user wants to deploy, ship, release, roll out, push live, or
  redeploy either app, e.g. "deploy substrateos-api", "ship the web app", "push
  the latest to prod", "redeploy everything", "release the admin changes". Handles the
  full pipeline: pre-flight (must be on main + pull latest), local amd64 image
  build, ACR push, container-app rollout, and post-deploy health verification.
---

# SubStrateOS Deploy

Deploys two Azure Container Apps in resource group `rg-company-brain-india`
(region centralindia), both from registry `cbrainindiaacr`:

| App | Source dir | Image repo | Container App | Health check |
|-----|-----------|-----------|---------------|--------------|
| Backend | `substrateos-api/` | `substrateos-api` | `substrateos-api` | `GET /healthz` → 200 `{"status":"ok","service":"substrateos-api"}` |
| Frontend | `web/` | `substrateos-web` | `substrateos-web` | `GET /` → 401 (behind Easy Auth = reachable) |

Public URLs:
- substrateos-api: `https://substrateos-api.redplant-161decbe.centralindia.azurecontainerapps.io`
- web: `https://substrateos-web.redplant-161decbe.centralindia.azurecontainerapps.io`

## This is a production action — confirm first

Both container apps are shared production. Before deploying, confirm with the user
**what to deploy** (`substrateos-api`, `web`, or `both`) and that they want it live now.
Azure CLI must be logged in (`az account show`) and Docker running. The
`az containerapp update` step may also trigger a permission prompt — that's
expected for a prod rollout.

## The fast path

Run the bundled script with the target (`substrateos-api`, `web`, or `both`; default `both`):

```bash
.claude/skills/substrateos-deploy/scripts/deploy.sh both
```

It performs every step below — pre-flight, build, push, rollout, and verify — and
aborts with a clear message if any step fails (wrong branch, failed revision, or a
failed health check). Read the section below so you understand what it does and can
fall back to manual commands if a step needs intervention.

## What the deploy does, and why

### 1. Pre-flight: be on `main` with the latest code
The script refuses to deploy from any branch other than `main`, then runs
`git pull --ff-only origin main`. We deploy what's on `main` so production always
matches the reviewed, merged history — deploying from a feature branch or stale
checkout is how prod and the repo silently diverge. If the working tree has
uncommitted changes it **warns** (the image would include code that isn't in any
commit) and tags the image `<sha>-dirty` so the drift is at least visible. If you
see the dirty warning, check `git status` and decide whether those changes should
ship before proceeding.

### 2. Build the image locally for `linux/amd64`
Server-side ACR builds are **disabled** on this registry (`az acr build` fails with
`TasksOperationsNotAllowed`), so images are built locally and pushed. Container Apps
run amd64, so on an Apple-Silicon Mac the build **must** pass
`--platform linux/amd64` (emulated — the first build is slow, but layers cache, so
subsequent builds of the same app are fast). The image tag is the short git SHA, so
every deployed image traces back to an exact commit.

### 3. Push to ACR and roll the container app
`az acr login --name cbrainindiaacr`, `docker push`, then
`az containerapp update -n <app> -g rg-company-brain-india --image <repo:tag>`,
which creates a new revision and shifts 100% traffic to it.

### 4. Verify the rollout
Poll the new revision until `provisioningState=Provisioned`, `runningState=Running`,
`trafficWeight=100` (abort on `Failed`), then hit the health endpoint:
- **substrateos-api** must return HTTP 200 from `/healthz` (unauthenticated).
- **web** is behind Easy Auth, so an unauthenticated request returns **401** — that
  means the container is up and serving. A `5xx` or a connection failure is the real
  failure signal. (You can't smoke-test the authed UI via curl; confirm Sync/Purge
  interactively by logging in.)

## Frontend build gotcha: env is baked at build time

Next.js bakes `NEXT_PUBLIC_*` variables into the bundle **at build time**, not
runtime — the `substrateos-web` container app sets none. The correct prod values
live in `web/.env.production` (committed: prod `NEXT_PUBLIC_API_BASE_URL`, empty
`NEXT_PUBLIC_DEBUG_AUTH`), which Next.js auto-loads during `pnpm build`. That file
must stay present and must **not** be excluded by `web/.dockerignore` (which
excludes `.env.local` but not `.env.production`). If the admin UI ever starts
calling `localhost:8000` in prod, this is why — the build didn't pick up
`.env.production`.

## Manual fallback (one app)

If the script aborts mid-way or you need to deploy a single app by hand (replace
`substrateos-api`/`substrateos-api` with `substrateos-web`/`web` for the frontend):

```bash
cd "$(git rev-parse --show-toplevel)"
git rev-parse --abbrev-ref HEAD          # must be: main
git pull --ff-only origin main
TAG=$(git rev-parse --short HEAD)
az acr login --name cbrainindiaacr
docker build --platform linux/amd64 -t cbrainindiaacr.azurecr.io/substrateos-api:$TAG -f substrateos-api/Dockerfile substrateos-api
docker push cbrainindiaacr.azurecr.io/substrateos-api:$TAG
az containerapp update -n substrateos-api -g rg-company-brain-india --image cbrainindiaacr.azurecr.io/substrateos-api:$TAG
# then verify:
curl -s -w '\n%{http_code}\n' https://substrateos-api.redplant-161decbe.centralindia.azurecontainerapps.io/healthz
```

## Notes

- Historical images used a manual `indiaN` tag scheme (e.g. `india13`); this skill
  tags by git SHA instead for traceability. Both coexist fine in the registry.
- The backend exposes `POST /admin/purge` (admin-key guarded) — a **destructive**
  "purge the tenant index" action. Deploying it is safe; running it wipes indexed
  data. Don't invoke a purge as part of a deploy.
- `minReplicas=1` on both apps (no scale-to-zero), so there's no cold-start gap, but
  the first request after a new revision pays a one-time warmup.
