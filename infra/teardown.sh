#!/usr/bin/env bash
set -euo pipefail
RG="${RG:-rg-company-brain-dev}"
read -r -p "Delete resource group $RG? [y/N] " confirm
[ "$confirm" = "y" ] && az group delete -n "$RG" --yes --no-wait
