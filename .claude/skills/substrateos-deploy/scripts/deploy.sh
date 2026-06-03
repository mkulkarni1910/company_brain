#!/usr/bin/env bash
# Build + deploy the SubStrateOS apps to Azure Container Apps, then verify health.
# Usage: deploy.sh [brain-api|web|both]   (default: both)
#
# Pre-flight enforces: on `main`, fast-forward pull, warn on dirty tree.
# Build is local linux/amd64 (server-side ACR builds are disabled on this registry).
# Image tag = short git SHA (so a deployed image traces to an exact commit).
set -euo pipefail

RG="rg-company-brain-india"
ACR="cbrainindiaacr"

TARGET="${1:-both}"
case "$TARGET" in
  brain-api|web|both) ;;
  *) echo "Usage: $0 [brain-api|web|both]" >&2; exit 2 ;;
esac

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# ---- 1. Pre-flight ----------------------------------------------------------
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "ABORT: on branch '$BRANCH', not 'main'. Deploy only from main." >&2
  exit 1
fi

echo "==> Pulling latest main (fast-forward only)..."
git pull --ff-only origin main

DIRTY=""
if ! git diff --quiet || ! git diff --cached --quiet; then
  DIRTY="-dirty"
  echo "WARNING: working tree has uncommitted changes — the image will include code"
  echo "         that is not in any commit. Review 'git status' before shipping." >&2
fi

TAG="$(git rev-parse --short HEAD)${DIRTY}"
echo "==> Deploying target='$TARGET' at tag '$TAG'"

echo "==> az acr login ($ACR)..."
az acr login --name "$ACR" >/dev/null

# ---- helpers ----------------------------------------------------------------
verify() {
  local name="$1" rev="$2" state code fqdn i
  echo "==> Waiting for revision '$rev' to be healthy..."
  for i in $(seq 1 36); do
    # `-o tsv` on an array projection returns one value per line, so collapse BOTH
    # tabs and newlines to single spaces (a bare `tr -d '\n'` would glue the fields
    # into "ProvisionedRunning100" and never match the check below).
    state="$(az containerapp revision show -n "$name" -g "$RG" --revision "$rev" \
      --query "[properties.provisioningState, properties.runningState, properties.trafficWeight]" \
      -o tsv 2>/dev/null | tr '\t\n' '  ' | sed 's/  */ /g; s/ *$//')"
    echo "    [$i] $state"
    # Healthy = Provisioned + a Running* state (Azure reports both "Running" and
    # "RunningAtMaxScale"); trafficWeight is informational, not part of the gate.
    case "$state" in
      "Provisioned Running"*) break ;;
      *Failed*|*Degraded*) echo "ABORT: revision '$rev' state: $state" >&2; exit 1 ;;
    esac
    sleep 5
  done

  fqdn="$(az containerapp show -n "$name" -g "$RG" \
    --query properties.configuration.ingress.fqdn -o tsv)"

  if [ "$name" = "brain-api" ]; then
    code="$(curl -s -m 20 -o /tmp/_deploy_health -w '%{http_code}' "https://$fqdn/healthz" || echo 000)"
    echo "==> brain-api /healthz -> $code $(cat /tmp/_deploy_health 2>/dev/null)"
    if [ "$code" != "200" ]; then echo "ABORT: brain-api health check failed." >&2; exit 1; fi
  else
    # web sits behind Easy Auth: 401 to an anonymous request means it's up.
    code="$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://$fqdn/" || echo 000)"
    echo "==> web / -> $code (200/302/401/403 = reachable; 401 expected behind Easy Auth)"
    case "$code" in
      200|301|302|401|403) ;;
      *) echo "ABORT: web health check failed (HTTP $code)." >&2; exit 1 ;;
    esac
  fi
  echo "==> OK: $name is live on $rev (https://$fqdn)"
}

deploy_one() {
  local name="$1" dir="$2" image="$3" ref rev
  ref="$ACR.azurecr.io/$image:$TAG"
  echo ""
  echo "================ $name ================"
  echo "==> Building $ref (linux/amd64)..."
  docker build --platform linux/amd64 -t "$ref" -f "$dir/Dockerfile" "$dir"
  echo "==> Pushing $ref..."
  docker push "$ref"
  echo "==> Rolling container app '$name'..."
  rev="$(az containerapp update -n "$name" -g "$RG" --image "$ref" \
    --query properties.latestRevisionName -o tsv)"
  verify "$name" "$rev"
}

# ---- 2-4. Build / push / roll / verify --------------------------------------
if [ "$TARGET" = "brain-api" ] || [ "$TARGET" = "both" ]; then
  deploy_one "brain-api" "substrateos-api" "brain-api"
fi
if [ "$TARGET" = "web" ] || [ "$TARGET" = "both" ]; then
  deploy_one "substrateos-web" "web" "substrateos-web"
fi

echo ""
echo "================ Deploy complete: target='$TARGET' tag='$TAG' ================"
