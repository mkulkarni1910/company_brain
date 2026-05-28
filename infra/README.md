# Infrastructure

## Provision

Requires: `az` CLI logged in (`az login`), Owner or Contributor on the subscription.

```
chmod +x provision.sh teardown.sh
./provision.sh
```

Run takes ~15 minutes (Redis is the slowest). Re-runs are idempotent.

Output: a block of `.env` values to copy into `../brain-api/.env`.

## Teardown

```
./teardown.sh
```
