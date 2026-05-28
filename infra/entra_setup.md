# Entra App Registration — Manual Setup

The `provision.sh` script handles all Azure resources, but **Entra app
registration must be done in the portal** because admin consent for delegated
permissions requires a human click.

Do this once per tenant. Estimated time: 15 min.

## 1. Register the API app (`brain-api`)

1. https://portal.azure.com → Entra ID → App registrations → New registration
2. Name: `brain-api`
3. Supported account types: "Accounts in this organizational directory only"
4. Redirect URI: leave blank
5. Register

Note the **Application (client) ID** — this is the `AZURE_API_CLIENT_ID`.

### Expose an API

1. Manage → Expose an API → Set → use default URI `api://{client-id}` → Save
2. Add a scope:
   - Scope name: `Query.Read`
   - Who can consent: Admins and users
   - Admin consent display name: "Query the company brain"
   - Admin consent description: "Allows the web app to call brain-api on behalf of the signed-in user"
   - State: Enabled
   - Add scope

### Add API permissions

1. API permissions → Add a permission → Microsoft Graph
2. **Application permissions** (used by brain-api for group expansion):
   - `Directory.Read.All` → Add permissions
3. **Delegated permissions** (used for Live Fetch via OBO — Day 5):
   - `Sites.Read.All`
   - `Files.Read.All`
4. **Grant admin consent for <tenant>** — click and confirm

## 2. Register the web app (`brain-web`)

1. App registrations → New registration
2. Name: `brain-web`
3. Supported account types: same tenant only
4. Redirect URI: Single-page application → `http://localhost:3000`
5. Register

Note the **Application (client) ID** — this is `NEXT_PUBLIC_AZURE_CLIENT_ID`.

### Configure auth

1. Authentication → Add platform → SPA → `http://localhost:3000`
2. Add `http://localhost:3000/auth/callback` if your library needs it
3. Save

### Add API permissions

1. API permissions → Add a permission → My APIs → `brain-api` → `Query.Read`
2. Grant admin consent for <tenant>

## 3. Verify

Run from `brain-api/`:

```
uv run python -c "
from azure.identity import DefaultAzureCredential
from msgraph.generated.models.user import User  # if available; otherwise httpx
import httpx
cred = DefaultAzureCredential()
tok = cred.get_token('https://graph.microsoft.com/.default').token
r = httpx.get('https://graph.microsoft.com/v1.0/users?\$top=1', headers={'Authorization': f'Bearer {tok}'})
print(r.status_code, r.json().get('value', [])[:1])
"
```

Expected: `200` and one user object. If 403, admin consent for `Directory.Read.All` did not take — re-grant it.

## Outputs

Add to `brain-api/.env`:

```
AZURE_API_CLIENT_ID=<brain-api app client id>
AZURE_API_SCOPE=api://<brain-api app client id>/Query.Read
```

Add to `web/.env.local`:

```
NEXT_PUBLIC_AZURE_TENANT_ID=<tenant id>
NEXT_PUBLIC_AZURE_CLIENT_ID=<brain-web app client id>
NEXT_PUBLIC_AZURE_API_SCOPE=api://<brain-api app client id>/Query.Read
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```
