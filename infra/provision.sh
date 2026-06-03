#!/usr/bin/env bash
# Bootstrap all Azure resources for Phase 1 of company-brain.
# Idempotent: re-running is safe (uses `--name`-based existence checks).
# Requires: az CLI logged in (`az login`), permissions to create resources.

set -euo pipefail

# ---- Edit before running ---------------------------------------------------
LOCATION="${LOCATION:-eastus2}"
RG="${RG:-rg-company-brain-dev}"
NAME_PREFIX="${NAME_PREFIX:-cbrain-$(whoami | tr '[:upper:]' '[:lower:]')}"
# ----------------------------------------------------------------------------

SEARCH_NAME="${NAME_PREFIX}-search"
OPENAI_NAME="${NAME_PREFIX}-openai"
REDIS_NAME="${NAME_PREFIX}-redis"
KV_NAME="${NAME_PREFIX}-kv"
ACR_NAME="$(echo "${NAME_PREFIX}acr" | tr -d '-')"  # ACR disallows hyphens
APPINSIGHTS_NAME="${NAME_PREFIX}-ai"
LOGS_NAME="${NAME_PREFIX}-logs"
MI_NAME="${NAME_PREFIX}-mi"
CAPP_ENV="${NAME_PREFIX}-capp-env"

echo "Provisioning into $RG ($LOCATION)..."

az group create -n "$RG" -l "$LOCATION" 1>/dev/null

# AI Search
if ! az search service show -g "$RG" -n "$SEARCH_NAME" &>/dev/null; then
  echo "Creating AI Search service $SEARCH_NAME..."
  az search service create -g "$RG" -n "$SEARCH_NAME" -l "$LOCATION" \
    --sku basic --replica-count 1 --partition-count 1 1>/dev/null
fi

# Azure OpenAI account + model deployments
if ! az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" &>/dev/null; then
  echo "Creating Azure OpenAI account $OPENAI_NAME..."
  az cognitiveservices account create -g "$RG" -n "$OPENAI_NAME" -l "$LOCATION" \
    --kind OpenAI --sku S0 --yes 1>/dev/null
fi
deploy_model() {
  local dep="$1" model="$2" version="$3" sku="$4" capacity="$5"
  if ! az cognitiveservices account deployment show -g "$RG" -n "$OPENAI_NAME" \
       --deployment-name "$dep" &>/dev/null; then
    echo "Deploying $dep ($model:$version)..."
    az cognitiveservices account deployment create -g "$RG" -n "$OPENAI_NAME" \
      --deployment-name "$dep" --model-name "$model" --model-version "$version" \
      --model-format OpenAI --sku-name "$sku" --sku-capacity "$capacity" 1>/dev/null
  fi
}
deploy_model "gpt-4o" "gpt-4o" "2024-11-20" "Standard" 30
# Plan-step deployment (orchestrator classifier) is deferred to Phase 2 when the
# plan step lands. Phase 1 orchestrator is cache → retrieve → answer; no planner
# call is made. When re-enabled, pick a current-GA mini model and request quota
# for it (`az cognitiveservices usage list --location <region>`).
# deploy_model "gpt-4-1-mini" "gpt-4.1-mini" "2025-04-14" "Standard" 30
deploy_model "text-embedding-3-large" "text-embedding-3-large" "1" "Standard" 30

# Redis
if ! az redis show -g "$RG" -n "$REDIS_NAME" &>/dev/null; then
  echo "Creating Redis $REDIS_NAME (Basic C0, ~10 min)..."
  az redis create -g "$RG" -n "$REDIS_NAME" -l "$LOCATION" \
    --sku Basic --vm-size c0 1>/dev/null
fi

# Key Vault
if ! az keyvault show -g "$RG" -n "$KV_NAME" &>/dev/null; then
  echo "Creating Key Vault $KV_NAME..."
  az keyvault create -g "$RG" -n "$KV_NAME" -l "$LOCATION" \
    --enable-rbac-authorization true 1>/dev/null
fi

# ACR
if ! az acr show -g "$RG" -n "$ACR_NAME" &>/dev/null; then
  echo "Creating ACR $ACR_NAME..."
  az acr create -g "$RG" -n "$ACR_NAME" --sku Basic 1>/dev/null
fi

# Log Analytics + App Insights
if ! az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" &>/dev/null; then
  echo "Creating Log Analytics $LOGS_NAME..."
  az monitor log-analytics workspace create -g "$RG" -n "$LOGS_NAME" -l "$LOCATION" 1>/dev/null
fi
LOGS_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" --query id -o tsv)

if ! az monitor app-insights component show -g "$RG" -a "$APPINSIGHTS_NAME" &>/dev/null; then
  echo "Creating App Insights $APPINSIGHTS_NAME..."
  az monitor app-insights component create -g "$RG" -a "$APPINSIGHTS_NAME" -l "$LOCATION" \
    --workspace "$LOGS_ID" 1>/dev/null
fi

# Managed identity for brain-api
if ! az identity show -g "$RG" -n "$MI_NAME" &>/dev/null; then
  echo "Creating managed identity $MI_NAME..."
  az identity create -g "$RG" -n "$MI_NAME" 1>/dev/null
fi
MI_PRINCIPAL=$(az identity show -g "$RG" -n "$MI_NAME" --query principalId -o tsv)
MI_CLIENT=$(az identity show -g "$RG" -n "$MI_NAME" --query clientId -o tsv)

# RBAC roles
SUB=$(az account show --query id -o tsv)
SEARCH_ID=$(az search service show -g "$RG" -n "$SEARCH_NAME" --query id -o tsv)
OPENAI_ID=$(az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" --query id -o tsv)
KV_ID=$(az keyvault show -g "$RG" -n "$KV_NAME" --query id -o tsv)

assign() {
  az role assignment create --assignee "$MI_PRINCIPAL" --role "$1" --scope "$2" 1>/dev/null || true
}
assign "Search Service Contributor" "$SEARCH_ID"
assign "Search Index Data Contributor" "$SEARCH_ID"
assign "Cognitive Services OpenAI User" "$OPENAI_ID"
assign "Key Vault Secrets User" "$KV_ID"

# Container Apps env
if ! az containerapp env show -g "$RG" -n "$CAPP_ENV" &>/dev/null; then
  echo "Creating Container Apps env $CAPP_ENV..."
  az containerapp env create -g "$RG" -n "$CAPP_ENV" -l "$LOCATION" \
    --logs-workspace-id "$(az monitor log-analytics workspace show -g "$RG" -n "$LOGS_NAME" --query customerId -o tsv)" \
    --logs-workspace-key "$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LOGS_NAME" --query primarySharedKey -o tsv)" \
    1>/dev/null
fi

# Print .env values
cat <<EOF

=== Done. Copy into substrateos-api/.env ===
AZURE_TENANT_ID=$(az account show --query tenantId -o tsv)
AZURE_CLIENT_ID=$MI_CLIENT
AZURE_AI_SEARCH_ENDPOINT=https://$SEARCH_NAME.search.windows.net
AZURE_AI_SEARCH_INDEX=substrateos-content-t-test
AZURE_OPENAI_ENDPOINT=$(az cognitiveservices account show -g "$RG" -n "$OPENAI_NAME" --query properties.endpoint -o tsv)
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_PLAN_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-large
AZURE_REDIS_HOST=$(az redis show -g "$RG" -n "$REDIS_NAME" --query hostName -o tsv)
AZURE_KEY_VAULT_URL=$(az keyvault show -g "$RG" -n "$KV_NAME" --query properties.vaultUri -o tsv)
APPLICATIONINSIGHTS_CONNECTION_STRING=$(az monitor app-insights component show -g "$RG" -a "$APPINSIGHTS_NAME" --query connectionString -o tsv)
EOF
