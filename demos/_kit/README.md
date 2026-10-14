# Serve7 demo kit

Small, self-contained client demos (an ACXD agent + its data API) that are created, changed and deleted from chat
through GitHub issues. Account 480315222980, ca-central-1. **Synthetic data only.**

## Layout of a demo (`demos/<name>/`)
| File | What it is |
|---|---|
| `demo.json` | name, stack (`demo-*`), region, Lambda dir, seed file, ACXD secret name, smoke op, tags |
| `template.yaml` | CloudFormation: tables + API Lambda (function URL, `x-api-key` from the shared secret) |
| `lambdas/api/handler.py` | the operations the ACXD data requests call (`POST /<op>`), plus `/health` (no key, counts only) |
| `data/seed.json` | synthetic rows per stack output table; `"+N"` dates = today + N days |
| `admin.py` | optional demo-specific data commands (`COMMANDS`) |
| `acxd/` | ACXD application, flow (Generative Journey) and data requests to import |
| `tests/` | offline tests (fake tables) |

## Operations (open an issue titled `demo: <command> <demo> [args]`)
- `deploy <demo>`: create or update the stack, seed empty tables, smoke test, print the data request URLs
- `status <demo>` · `seed <demo>` · `reset <demo>` (reload the seed) · `test <demo>`
- admin commands, e.g. `set-price yamaha-moto mt07 70`, `maintenance yamaha-moto YM-MT07-02 on`
- `delete <demo>`: the issue body must contain `confirm delete <demo>`. This removes the stack, its data and its code. The ACXD app is removed in ACXD.

The workflow (`.github/workflows/demo.yml`, source `demos/_kit/demo.workflow.yml`) posts the output as a comment.
It uses the CI role `serve7-ccg-s5-ci` (main branch only); policy `demo-kit` lets it manage `demo-*` IAM roles only.

## API key
Demos reference the existing secret `ccg-s5/acxd-api-key` by ARN (fallback `demo-kit/acxd-api-key`), so the ACXD
workspace secret `ccg_s5_api_key` works for every demo. Nothing reads or prints the key value. Note: tearing down
ccg-s5 schedules that secret for deletion. After that, demos fall back to a new `demo-kit/acxd-api-key`, which then needs its own ACXD secret.

## ACXD (manual, once per demo)
Import the data requests (with this stack's URL, printed by `deploy`), then the flow and the application. Then click **Build & deploy**, which stays a human click.
