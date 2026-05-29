# Free Azure Data Explorer (ADX) Cluster — Manual Setup

ADX holds the Activity pillar's event stream. The **free cluster** is created
via the Azure Data Explorer web UI (not `az` CLI) and costs nothing.

Do this once. ~5 min.

## 1. Create the free cluster

1. Go to https://dataexplorer.azure.com/freecluster
2. Sign in with the same identity used for `az login`
   (companybrain.microsoft@gmail.com).
3. Click **Create cluster free**. Accept defaults. Wait ~2 min.
4. Note the **Cluster URI** shown on the cluster page — looks like
   `https://<name>.<region>.kusto.windows.net` (classic) or
   `https://trd-<token>.<region>.kusto.fabric.microsoft.com` (Fabric free).
   This is `ADX_CLUSTER_URI`.

## 2. Create the database

1. In the web UI, on your free cluster, click **Create database**.
2. Name it `brain`. Create.
   This is `ADX_DATABASE=brain`.

## 3. Confirm your identity is admin

The creating identity is automatically Database Admin on a free cluster, so
`DefaultAzureCredential` (your `az login` identity) can create tables and
ingest. No extra role assignment needed. If a later step gets a 403 (Forbidden)
on `.create table`, open the database → Permissions → add yourself as Admin.

## Outputs

Add to `brain-api/.env`:

```
ADX_CLUSTER_URI=<cluster uri from step 1>
ADX_DATABASE=brain
```
