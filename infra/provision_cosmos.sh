#!/usr/bin/env bash
# Provision Cosmos DB (Gremlin API) for the People pillar. Idempotent.
# Requires az logged in. Uses the same RG/region as Phase 1.
set -euo pipefail

LOCATION="${LOCATION:-swedencentral}"
RG="${RG:-rg-company-brain-dev}"
NAME_PREFIX="${NAME_PREFIX:-cbrain-$(whoami | tr '[:upper:]' '[:lower:]')}"

COSMOS_NAME="${NAME_PREFIX}-cosmos"
DB_NAME="brain"
GRAPH_NAME="people"

echo "Ensuring Microsoft.DocumentDB provider is registered..."
az provider register --namespace Microsoft.DocumentDB 1>/dev/null
until [ "$(az provider show --namespace Microsoft.DocumentDB --query registrationState -o tsv)" = "Registered" ]; do
  echo "  ...waiting for DocumentDB provider registration"
  sleep 15
done

if ! az cosmosdb show -g "$RG" -n "$COSMOS_NAME" &>/dev/null; then
  echo "Creating Cosmos DB Gremlin account $COSMOS_NAME (serverless)..."
  az cosmosdb create -g "$RG" -n "$COSMOS_NAME" -l "$LOCATION" \
    --capabilities EnableGremlin EnableServerless \
    --default-consistency-level Session 1>/dev/null
fi

if ! az cosmosdb gremlin database show -g "$RG" -a "$COSMOS_NAME" -n "$DB_NAME" &>/dev/null; then
  echo "Creating Gremlin database $DB_NAME..."
  az cosmosdb gremlin database create -g "$RG" -a "$COSMOS_NAME" -n "$DB_NAME" 1>/dev/null
fi

if ! az cosmosdb gremlin graph show -g "$RG" -a "$COSMOS_NAME" -d "$DB_NAME" -n "$GRAPH_NAME" &>/dev/null; then
  echo "Creating Gremlin graph $GRAPH_NAME (partition key /tenant_id)..."
  az cosmosdb gremlin graph create -g "$RG" -a "$COSMOS_NAME" -d "$DB_NAME" -n "$GRAPH_NAME" \
    --partition-key-path "/tenant_id" 1>/dev/null
fi

GREMLIN_KEY=$(az cosmosdb keys list -g "$RG" -n "$COSMOS_NAME" --query primaryMasterKey -o tsv)

cat <<EOF

=== Done. Copy into brain-api/.env ===
COSMOS_GREMLIN_ENDPOINT=wss://${COSMOS_NAME}.gremlin.cosmos.azure.com:443/
COSMOS_GREMLIN_KEY=${GREMLIN_KEY}
COSMOS_GREMLIN_DATABASE=${DB_NAME}
COSMOS_GREMLIN_GRAPH=${GRAPH_NAME}
EOF
