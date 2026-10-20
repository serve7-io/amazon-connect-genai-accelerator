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
- `web <demo>`: re-publish the demo's web test page only (see below)
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

## Web test page (CloudFront)
Every demo with a `"web"` block in `demo.json` gets a test page at `https://<cloudfront domain>/<demo>/` with the ACXD
chat (Amazon Connect Touchpoint) and, optionally, Live Sync. `deploy` publishes it as step 6 of 6; `demo: web <demo>` republishes it.

How it works:
- **One shared stack, `demo-kit-web`** (`web/stack.yaml`): a private S3 bucket behind one CloudFront distribution (HTTPS only, default
  `*.cloudfront.net` domain, `X-Robots-Tag: noindex`, `robots.txt` disallow all). Each demo is a folder `/<demo>/`. The stack is created
  by the first demo that needs it and kept when a demo is deleted.
- **The chat goes through Amazon Connect**, not straight to ACXD: Touchpoint starts a Connect chat contact, and a Connect chat flow with
  the Agentic CX block hands it to the ACXD app. So each demo template has a conditional `ChatFlow` (created only when the Connect
  instance, ACXD workspace, application id and the app's Production deployment key are all known).
- **`POST /<demo>/start-chat`** is a small Lambda on the same CloudFront domain (`web/start_chat/`). It is keyless like the AWS sample, but
  locked down: the instance and flow come from SSM `/demo-kit/web/<demo>/chat` (written by `deploy-web.sh`), the browser can only
  choose the display name, and only demos deployed through the kit can start a chat.
- **Touchpoint** (`@amazon-connect-touchpoint/web`) is pinned to 1.0.4 with a Subresource Integrity hash (`web/render.py`).

The `web` block in `demo.json`:
| Key | What |
|---|---|
| `title`, `brand`, `blurb`, `prompts` | Page text; `prompts` are the "Try asking" suggestions |
| `assistant_name`, `display_name`, `accent`, `language` | Chat widget identity, default visitor name, accent colour, language code |
| `connect_instance_id` | The Connect instance that runs the chat |
| `acxd.workspace_id`, `acxd.application_id` | The ACXD workspace and the demo's application (ids only, no secrets) |

Optional `demos/<demo>/web/actions.js` is inserted after Touchpoint starts and can use the variable `touchpoint`.

### One-time setup per account
1. **CI role.** The role `serve7-ccg-s5-ci` needs CloudFront permissions (policy `demo-kit-web` in `demos/ccg-s5/ci/github-trust.yaml`). Re-run in CloudShell
   of the demo account: `bash demos/ccg-s5/ci/setup-github-trust.sh`. Until then the web step of a deploy warns and is skipped, the API still deploys.
2. **Keys per demo.** In CloudShell: `bash demos/_kit/web/set-acxd-keys.sh <demo>`. It asks (hidden) for the app's Production deployment key
   and, optionally, the Live Sync API key, and stores them in Secrets Manager `demo-kit/web/<demo>/acxd`.
   **Never put these keys in the repo, an issue or a chat.** They come from the ACXD app's Deploy tab or Settings / Access.
3. **Workflow copy.** `.github/workflows/demo.yml` must be identical to `demos/_kit/demo.workflow.yml`: after merging, run
   `cp demos/_kit/demo.workflow.yml .github/workflows/demo.yml` and commit it (only a person with the `workflow` permission can push that folder).
   `deploy` already runs the web step without it; the `web <demo>` command and the PR check of the kit's web tests need the new copy.

### Checklist for a new demo
1. Copy a demo folder, adapt the code and data, and add a `web` block (without `acxd.application_id` at first).
2. `demo: deploy <demo>`, then import the ACXD data requests, flow and app, and click Build & deploy.
3. Add `web.acxd.application_id` to `demo.json` (PR) and run `set-acxd-keys.sh <demo>`.
4. `demo: deploy <demo>` again: the chat flow is created, the chat is wired and the page is published. The comment shows the page URL.

### Live Sync
The page passes `liveSync: {deploymentKey, apiKey}` to Touchpoint when both keys are stored. For Live Sync to do anything the ACXD app needs a
**Live Sync node** and the page needs actions (`web/actions.js`, using `touchpoint.sendContext({actions: [...]})`). A plain Generative Journey app
works as a normal chat. Note that the keys end up in the public page (the ACXD Deploy snippet does the same), so use demo-only keys.

### Security notes
The pages are public to anyone with the link (not indexed). They contain no data of their own, and all demo data is synthetic. `start-chat`
creates a Connect chat contact per visitor, so share the link only as widely as you are happy to pay for chats.
`demo: delete <demo>` removes the page and the chat wiring but keeps the shared stack and the key secret.

## Ship without clicks (auto-ship)

Opt-in pipeline for small demo changes: `.github/workflows/auto-ship.yml` (source copy `auto-ship.workflow.yml`, logic in
`auto_ship.py`, tests in `tests/test_auto_ship.py` and `tests/test_workflows.py`).

1. As the owner (`OWNERS` in the workflow), push a branch named `auto/<anything>`.
2. The branch is merged into a copy of `main` and every check runs on the result: each demo's tests and `cfn-lint`,
   the kit tests, the shared web stack lint, `bash -n` on the kit scripts.
3. If the merge only touches `demos/` (never `demos/ccg-s5/`), `docs/` or `README.md` and the checks pass, it is pushed
   to `main` and the branch is deleted. Changed demos listed in `AUTO_DEPLOY` (a kit change counts for all of them)
   are deployed by dispatching `demo.yml` with `deploy <demo>`.
4. Anything else (merge conflict, files outside those folders, failing checks) is not merged; a pull request is opened
   for review. Opening it needs Settings > Actions > General > "Allow GitHub Actions to create and approve pull
   requests"; without it the run only logs the reason.

Notes:
- `demo.yml` must allow the actor `github-actions[bot]` (`ALLOWED_ACTORS`), because the dispatch uses `GITHUB_TOKEN`.
- The live workflow files must stay identical to their source copies (`test_live_workflows_match_their_source_copies`).
- A branch that edits `auto-ship.yml` runs its own version of the workflow, so auto-ship is not a defence against
  someone who can already push to the repo. It only saves clicks for the owner.
