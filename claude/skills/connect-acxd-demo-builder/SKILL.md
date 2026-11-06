---
name: connect-acxd-demo-builder
description: Build, deploy and operate Amazon Connect + ACXD client demos with the Serve7 demo kit in your own AWS account and GitHub repo: setup, AI chat, Live Sync, voice and live-agent hand-off.
---

# Connect + ACXD demo builder (Serve7 demo kit)

Use this skill to set up the Serve7 demo kit in a new AWS account and GitHub repo, to create a new client demo
(an AI agent with its own data API and a public test page), or to operate and debug an existing demo.
The kit lives in `demos/_kit/` of the repo `serve7-io/amazon-connect-genai-accelerator`. Every demo is a folder
`demos/<name>/` deployed by opening a GitHub issue titled `demo: <command> <name>`.

Reference demos to copy from:
- `demos/renault-rental`: Live Sync (the agent drives the web page), chat and voice on a CloudFront page. Most complete.
- `demos/yamaha-moto`: plain Generative Journey chat agent. Simplest.
- `demos/ccg-s1`: AI chat that hands over to a live agent with a step-by-step guide (server-side intake record).
- `demos/maple-health`: kit web page + identity check by **email one-time code** (simulated inbox on the page), EN/FR,
  appointments, hand-off to a live-agent queue with a guide card. Best template for "verify, then self-serve, then
  escalate" demos.
- `demos/ccg-s5`: voice payment with its own workflow (`ccg-s5.yml`); not part of the kit, never auto-shipped.

## 0. Ground rules (always)

- **Synthetic data only.** Invented names, numbers, prices. Mark invented business facts with `demo_value: true`.
- **No secrets in chat, issues, PRs or the repo** (the repo may be public). API keys, ACXD deployment keys and Live
  Sync keys go into Secrets Manager through CloudShell scripts that prompt with hidden input. Never read or print a
  secret value; a workspace hook may block `get-secret-value` in your shell anyway. Use CloudFormation
  `{{resolve:secretsmanager:...}}` references or the kit scripts.
- **Human clicks** (never work around a refusal or a permission classifier):
  - ACXD **Build & deploy** is always the user's click;
  - creating or editing files in `.github/workflows/` (the GitHub connector cannot write there);
  - repo security settings;
  - logging in to AWS or Connect.
- **Costs:** a demo runs on DynamoDB on-demand, Lambda and CloudFront (cents when idle) plus the Connect instance and
  any claimed phone number. Tear down with `demo: delete <name>` or `bash tools/account-shutdown.sh` (person).
- **Ask before deleting** anything (`demo: delete`, stacks, ACXD resources) and before anything that costs money or
  touches a client or production account. Never deploy the full Terraform accelerator into a shared or client account.
- Do not extract session tokens from the ACXD UI's internal API (`/acxd/api/...`). Use the UI. Reading the responses
  of saves that the UI itself made (network panel) to learn a JSON format is fine.
- **Merges to `main` can deploy.** `.github/workflows/deploy.yml` deploys the accelerator's CI tenants (repository
  variable `CI_TENANTS` or `DEFAULT_TENANT`; skipped while `AWS_CI=false`) on every push to main that touches
  `stacks/`, `modules/`, `flows/`, `lambdas/`, `tenants/`, `scripts/` or `bootstrap/`. `demos/**`, `docs/**`,
  `claude/**` and `tools/**` never trigger it. Before merging anything outside those, say so and check the run.
- GitHub "Update branch" on a PR is a commit to someone's branch: ask first.
- Never overwrite another person's ACXD apps, flows or data requests. Give your resources a unique prefix
  (`rn`, `s1`, ...): the ACXD workspace is shared and names must be unique.

## 1. Architecture

```
Browser test page (CloudFront, Touchpoint SDK 1.0.4 pinned with SRI)
  Chat: POST /<demo>/start-chat  -> start-chat Lambda (SSM /demo-kit/web/<demo>/chat)  -> Connect StartChatContact
  Talk: POST /<demo>/start-voice -> same Lambda (SSM /demo-kit/web/<demo>/voice)       -> Connect StartWebRTCContact
Connect chat/voice flow (stack demo-<name>) -- ConnectParticipantWithAgenticCX block --> ACXD app (Production key)
ACXD flow: Start -> Generative Journey or Live Sync node (tools = data requests) -> Done / Escalate
Data requests -> demo API Lambda (function URL, header x-api-key) -> DynamoDB tables (synthetic seed)
Live Sync page actions: touchpoint.sendContext({actions}) in demos/<demo>/web/actions.js
```

Stacks: `demo-<name>` per demo (tables, API Lambda, chat + voice flows), `demo-kit-web` shared (S3 + CloudFront +
start-chat Lambda), artifacts bucket `demo-kit-artifacts-<account>-<region>`.
CI: `.github/workflows/demo.yml` (source copy `demos/_kit/demo.workflow.yml`) assumes an OIDC role (no access keys)
that only trusts the `main` branch.

## 2. One-time setup in a new account (do this with the user)

### 2.1 What the user needs
- An AWS account where they are admin, a region with Amazon Connect and ACXD (the original uses `ca-central-1`).
- A GitHub org or user with Actions enabled, and the GitHub connector for Claude authorised on the repo.
- The Claude desktop app with the built-in browser (for ACXD and the Connect console). Claude in Chrome is optional
  (useful for the GitHub web editor).
- AWS CloudShell for the few steps that handle secrets or IAM trust.

### 2.2 Get your own copy of the repo
Follow `docs/start-here-with-claude.md` section 1 (read access to the source, push a git copy into an empty private
repo, disable the demo workflows until 2.6, upload this skill). The newest copy of this skill is in the repo at
`claude/skills/connect-acxd-demo-builder/SKILL.md`.

Since the November 2026 shutdown nothing of the demo kit runs in Serve7's accounts: every demo is rebuilt from the
repo with `demo: deploy`, and its ACXD resources are re-imported from `demos/<name>/acxd/`.

Then get the numeric repository id (GitHub API `GET /repos/<org>/<repo>` → `id`), which the OIDC trust pins.

### 2.3 Replace the account-specific values
Make these edits on a branch, then PR → merge (section 3.2):

| File | Value to change |
|---|---|
| `demos/ccg-s5/ci/github-trust.yaml` | `GitHubOrg`, `GitHubRepo`, `GitHubRepoId` defaults; description text |
| `demos/ccg-s5/ci/setup-github-trust.sh` | comment only (account / region) |
| `demos/_kit/demo.workflow.yml` | `CI_ROLE` (role ARN from 2.5), `AWS_REGION`, `AWS_DEFAULT_REGION`, `ALLOWED_ACTORS` (the user's GitHub login + `github-actions[bot]`), header comment |
| `demos/_kit/auto-ship.workflow.yml` | `OWNERS` (GitHub login), `AUTO_DEPLOY` (demo names) |
| `demos/_kit/tests/test_workflows.py` | the expected login in `OWNERS` / `ALLOWED_ACTORS` assertions |
| `demos/_kit/web/set-acxd-keys.sh`, `deploy.sh` | default region if not `ca-central-1` |
| each `demos/<demo>/demo.json` | `region`, `web.connect_instance_id`, `web.acxd.workspace_id`, `web.acxd.application_id` (remove the old app id until 3.6) |
| each `demos/<demo>/template.yaml` | any hard-coded instance ARN default (for example `ccg-s1` `ConnectInstanceArn`, `AcxdWorkspaceId`, `AcxdApplicationId`) |

The CI role keeps its name `serve7-ccg-s5-ci` unless you also rename it in `github-trust.yaml` (RoleName and the
Deny statement) and in `CI_ROLE`. Search the repo for the old account id, instance id and workspace id before you
finish (`grep -rn "<old id>"`).

### 2.4 Amazon Connect instance
- Create an instance (console, or `cloudformation/connect-phase1/phase1.sh` from the repo). Note its **instance id**
  and **alias** (the console domain is `https://<alias>.my.connect.aws`).
- Turn on chat (it is on by default) and flow logs. For hand-off demos: a queue, a routing profile and an agent user
  (the user creates users; never set or see passwords).
- The user signs in to the Connect admin site: AWS console → the instance → "Log in for emergency access".

### 2.5 GitHub → AWS trust (CloudShell, user runs it)
After the PR from 2.3 is merged, in CloudShell of the account:
`git clone <repo> && cd <repo> && bash demos/ccg-s5/ci/setup-github-trust.sh`
It creates the GitHub OIDC provider (or reuses one) and the role with these policies:
- `ccg-s5-operate`: CloudFormation, Lambda, DynamoDB, S3, logs, SSM, Secrets Manager, Connect; IAM only on `role/ccg-s5-*`;
- `demo-kit`: IAM only on `role/demo-*`, which is why every demo stack is named `demo-*`;
- `demo-kit-web`: CloudFront.

It is safe to re-run. Do not pass `CreateOidcProvider=false` by hand when this stack owns the provider: that deletes
it and every run then fails with "Could not assume role with OIDC". Put the output `RoleArn` into `CI_ROLE`.

### 2.6 Workflow files (human, GitHub web editor or git)
With a git clone that can push workflows (the start-here copy can), the user runs, commits and pushes:
```bash
cp demos/_kit/demo.workflow.yml .github/workflows/demo.yml
cp demos/_kit/auto-ship.workflow.yml .github/workflows/auto-ship.yml
```
Then they re-enable the `demo` and `auto-ship` workflows in Actions (they were disabled in 2.2).

Otherwise: `.github/workflows/demo.yml` must be byte-identical to `demos/_kit/demo.workflow.yml`, and the same goes for
`auto-ship.yml` and `auto-ship.workflow.yml` (a test compares them). The connector cannot write that folder. Either
the user copies the file in the GitHub web editor (Add file → Create new file → `.github/workflows/demo.yml` → paste →
commit to main), or Claude in Chrome does it as the user: open
`https://github.com/<org>/<repo>/new/main?filename=.github/workflows/demo.yml`, set the editor text with
`document.querySelector('.cm-content').cmTile.view.dispatch({changes:{from:0,to:view.state.doc.length,insert:TEXT}})`,
then commit. Optional for auto-ship review PRs: the user turns on Settings → Actions → General → "Allow GitHub Actions
to create and approve pull requests".

### 2.7 ACXD workspace and API key
- Open `https://<alias>.my.connect.aws/acxd`; the first visit offers "Join workspace". The **workspace id** is in the
  URL (`/acxd/w/<workspace id>/...`).
- API key shared by all demo APIs: on the first `demo: deploy`, `deploy.sh` reuses `ccg-s5/acxd-api-key` or creates
  `demo-kit/acxd-api-key` (random value). The user copies that value from the Secrets Manager console into an ACXD
  secret (Resources → Secrets), for example `demo_api_key`, and every demo's `demo.json` sets
  `"acxd_secret": "demo_api_key"`. Data request headers reference it as `{demo_api_key:NLX.Secret}`.
- Models: the ACXD node offers Nova 2 Lite, Haiku 4.5 and Sonnet 5 (`anthropic.claude-sonnet-5`). Sonnet 5 handled
  relative dates and language switching best; Nova Lite guessed dates and drifted languages.

## 3. Create a new demo (repeat per client)

### 3.1 Scaffold
Copy `demos/renault-rental` (web page + Live Sync) or `demos/yamaha-moto` (plain chat) to `demos/<name>`
(lowercase, digits and dashes). Then edit:
- `demo.json`: name, title, `stack: demo-<name>`, region, `seed`, `acxd_secret`, `status_tables`, tags, `smoke.op`,
  and the `web` block (title, brand, blurb, prompts, assistant name, accent, language, Connect instance id, ACXD
  workspace id; leave `application_id` out for now, the kit tests allow that). `web.page` is an optional object
  passed to the page as `CFG.page` (for example an API URL that `web/actions.js` polls; fill it after the first
  deploy from the stack outputs).
- `template.yaml`: table names (`<name>-*`), outputs named like the keys in `seed.json` `tables`. Keep the parameters
  `ConnectInstanceId`, `AcxdWorkspaceId`, `AcxdApplicationId`, `AcxdAlias` and the `HasChat` condition. Keep the chat
  flow's `greet` MessageParticipant before the Agentic CX block, and keep `SpeechRecognitionConfiguration` +
  `AudioFillerConfiguration` on that block (section 6).
- `lambdas/api/handler.py`: one function per operation (`POST /<op>`). Every response has a ready-to-say `message`;
  errors add `error` and, where useful, `valid_options`. Keep `/health` (no key, counts only) and the `x-api-key`
  check with `hmac.compare_digest`. Never trust ids from the model when the Connect contact id is available: take it
  from `nlx_context`.
- `data/seed.json`: synthetic rows per table; `"+N"` dates mean today + N days. Seed some "no" answers (sold out,
  maintenance) so the demo shows the agent handling them.
- `admin.py`: optional `COMMANDS` for `demo: <command> <name> [args]` (command names: letters and dashes only).
- `acxd/datarequests/*.datarequest.json`, `acxd/<flow>.flow.json`, `acxd/<app>.application.json` (formats: section 7).
- `web/`: `body.html` (extra cards; generate from the seed with a `make_body.py` when it lists data), `extra.css`,
  `actions.js` (runs after Touchpoint starts: Live Sync actions, or plain page scripts such as polling a demo API;
  `textContent` only). A demo API called from the page needs CORS on its function URL (`Cors: {AllowOrigins: ["*"],
  AllowMethods: [GET]}`) and must expose only synthetic data.
- `tests/`: offline tests with a fake table class (copy `test_api.py` and `test_web_page.py` and adapt).
- `README.md`: story, operations, invented values.

Run locally, one folder per pytest process (same-named test files collide):
`for d in _kit <name>; do python3 -m pytest -q -p no:cacheprovider demos/$d/tests; done; cfn-lint demos/<name>/template.yaml`

### 3.2 Push, PR, merge
- Push with the GitHub connector: `create_branch`, then `push_files` with the full file contents (several commits
  for big files is fine).
- Verify: `git clone` the branch (read access works) and `git show FETCH_HEAD:<path> | cmp - <local>` for every file.
- `create_pull_request`, wait for the checks `test` (~30 s) and `static` (~2 min) with `pull_request_read
  get_check_runs`, then `merge_pull_request` (squash). Never merge on red.
- Or use auto-ship (section 5) for changes that only touch `demos/`, `docs/` or `README.md`.

### 3.3 First deploy
Open an issue (connector `issue_write create`) titled `demo: deploy <name>`. In about 2–6 minutes `github-actions[bot]`
comments with the result (`issue_read get_comments`). The deploy does:
1. the API key secret;
2. the Lambda zip;
3. CloudFormation;
4. the seed (empty tables only);
5. the smoke test and the data request URLs;
6. the web page (without an app id the page shows "chat not ready").

### 3.4 ACXD resources (Claude in the built-in browser, user logged in)
1. **Data requests:** Resources → Data requests → open any → Settings → Import → choose the file → **Import as new**
   → Done. Use "Replace" + Save only to update one you own. External, POST, URL = API URL + `/<op>`, headers
   `Content-Type: application/json` and `x-api-key: {<secret>:NLX.Secret}`, **Send context on** (that is what
   delivers `nlx_context` with `contact_id`). Response models are generated from sample responses.
2. **Context variables** (workspace level, "Allow frontend updates" on): at least `contact_id` and `channel`.
3. **Flow:** Resources → Flows → any flow → Settings → Advanced → Import → **Import as new flow**.
4. **Application:** Applications → New application → attach the flow and set it as Welcome (and Fallback /
   Escalation default behaviours) → Save. Do not use "Build anyway".
5. **The user clicks Deploy → Build & deploy (Production).** Then read the **application id** from the URL and ask
   the user for the **Production deployment key** only through the CloudShell script below (never in chat).

### 3.5 Store the browser keys (user, CloudShell)
`bash demos/_kit/web/set-acxd-keys.sh <name>` prompts (hidden) for the Production deployment key and, for Live Sync,
the Live Sync API key (ACXD app → Deploy tab or Settings / Access), and stores them in Secrets Manager
`demo-kit/web/<name>/acxd`. `deploy.sh` reads the deployment key from there for any template with an `AcxdAlias`
parameter. Run it from a fresh clone in CloudShell (`git clone <repo> && cd <repo>` first).

### 3.6 Second deploy and test
- Add `web.acxd.application_id` to `demo.json` (PR → merge), then `demo: deploy <name>` again. Demos without a `web`
  block (own page served by the API Lambda, like ccg-s1) set top-level `"acxd_application_id"` instead. This creates
  the chat (and voice) flow, wires SSM, and publishes the page. The comment shows the page URL
  (`https://<cloudfront>/<name>/`, `?mode=voice` opens voice).
- Test in the browser: ask in each language, check the tool answers against the seed, book and cancel, check page
  actions. Each chat creates a Connect contact. Then run `demo: reset <name>`.
- Hand-off demos: escalate in the chat, then run `demo: status <name>`. The intake table must hold one row keyed by the
  chat's contact id with every field the tools returned (verified on ccg-s1, 20 Oct 2026). With no agent available the
  chat waits in the queue; a "busy" message means the transfer failed.
- ACXD's Test panel (app → Test) uses the saved flow without a deploy: good for prompt and model checks (no page
  actions there).

### 3.7 Operate
Issue titles:
- `demo: status|seed|reset|test|events <name>`;
- `demo: web <name>` (republish the page only);
- admin commands from `admin.py`;
- `demo: delete <name>`: the body must contain `confirm delete <name>`. **Always ask the user first.** It keeps the
  shared web stack and the key secret; the ACXD app is deleted in ACXD by the user.

## 4. Live Sync demos (agent drives the page)

- The ACXD node type is `multimodal` with `metadata.multimodal = {type: "agentic", prompt, tools, actions: [],
  exitConditions: [], maxTokens, maxSteps, temperature, modelType, ...}`.
- Page actions are SDK-defined: `touchpoint.sendContext({scopes, actions:[{action, description, schema, handler}]})`
  in `web/actions.js`. Nothing is declared on the ACXD node.
- The prompt must have a "Page actions" section with one `- <action>: ...` line per action (a test checks it). Tell
  the agent to call actions in the same turn and as soon as one detail is known; only panels showing prices or
  confirmations need tool results first.
- Handlers only change the page with `textContent` (never `innerHTML`); they never call the API.
- **The Live Sync node does not speak first.** Without a greeting the chat shows "Thinking..." forever. The Connect chat
  flow sends the greeting (`greet` MessageParticipant) and the prompt says "never greet again".
- Keep replies short and plain (no lists or markdown) so they also work in voice.
- Keys end up in the public page source by design (browser-side Live Sync); use demo-only keys.

## 5. Ship without clicks (auto-ship)
- Once `.github/workflows/auto-ship.yml` exists, the owner pushes a branch `auto/<anything>`.
- The workflow merges it into a copy of `main`, refuses anything outside `demos/` (except `demos/ccg-s5/`), `docs/`
  and `README.md`, runs every demo's tests, cfn-lint and the kit tests, then pushes to `main`, deletes the branch and
  dispatches `demo.yml deploy <demo>` for changed demos in `AUTO_DEPLOY`.
- Anything else gets a review PR (needs the repo setting from 2.6).
- `demo.yml` must allow the actor `github-actions[bot]`. If the dispatched run says "X may not run demo operations",
  add the name it prints to `ALLOWED_ACTORS`.

## 6. Hard-won facts (each cost time)
- **Agentic CX block in a Connect flow** needs `"SpeechRecognitionConfiguration": {"SpeechRecognitionEngine":
  "AMAZON_AGENTIC_VOICE"}` and `"AudioFillerConfiguration": {"Enabled": false}` **even for chat**, or Connect rejects
  the flow (`InvalidContactFlowException`).
- **CloudFormation only says `InvalidContactFlowException`.** Get Connect's real problem list by creating the rendered
  flow as a throwaway (`create_contact_flow`, then delete) from CI: `demos/_kit/web/check_chat_flow.py <demo>` for web
  demos, or an admin command like ccg-s1's `demo: check-chat-flow ccg-s1`, which swaps one block at a time for a plain
  message and prints ACCEPTED/REJECTED per variant. One run usually names the guilty block and property.
- **Routing criteria / proficiency routing:** `UpdateContactRoutingBehavior` with `Parameters.RoutingCriteria` is
  rejected in flow JSON ("Invalid Action property name"); Connect lists only `QueuePriority`,
  `QueueTimeAdjustmentSeconds`, `RoutingProficiencies` there, and that block accepts no `Errors` branch. Guessed
  `RoutingProficiencies` shapes were rejected too. Leave proficiency routing out of generated flows; if needed, build
  "Set routing criteria" once in the console, export the flow and copy the exact JSON.
- **`aws cloudformation deploy` keeps a parameter's previous value** when it is not overridden, even if the template
  default changed. Pass values explicitly (the kit passes `acxd_application_id` from `demo.json` as
  `AcxdApplicationId`, and the deployment key from the secret as `AcxdAlias`). A deploy that prints "No changes" after
  you changed a default is this.
- Context variables passed from Connect: `{"contact_id": "$.InitialContactId", "channel": "CHAT"}`.
- **Hand-off to a live agent with data:** ACXD Escalate-node state modifications cannot use tool results ("placeholders
  no longer supported"). Pattern that works (ccg-s1, ccg-s5):
  1. The API Lambda takes `contact_id` from `nlx_context` (never from the model) and merges each successful tool result
     into an intake table keyed by contact id.
  2. After the Agentic CX block returns on its `Escalation` branch, the Connect flow invokes the Lambda (`op=intake`,
     `contact_id=$.InitialContactId`) and sets contact attributes from `$.External.*`.
  3. Then set the queue → transfer (no routing-criteria block, see above).
  4. A step-by-step guide (`UpdateContactEventHooks` `DefaultAgentUI` → a flow using `ShowView` with the AWS-managed
     views `arn:aws:connect:<region>:aws:view/form:1` and `confirmation:1`) shows the agent the case and runs dispatch
     and write-back.
- An ACXD exit only fires when the model says nothing in that turn: put goodbye text on the Exit/Escalate node or in the
  Connect flow, and tell the model "exits are silent".
- Touchpoint endpoints:
  - chat: POST JSON `{InstanceId, ContactFlowId, ParticipantDetails:{DisplayName}, ...}`, response
    `{"data":{"startChatResult":{ContactId, ParticipantId, ParticipantToken}}}`;
  - voice: POST (`text/plain`, JSON body), response `{"data":{"startWebRtcResult":{ConnectionData:{Meeting,Attendee},
    ContactId, ParticipantId, ParticipantToken}}}`.

  The kit Lambda fixes the instance and flow server-side; the browser chooses only the display name.
- The Touchpoint widget lives in a closed shadow root: `find`/`read_page` cannot see it. Drive it by coordinates from a
  fresh screenshot, and expect screenshots to lag (check `read_network_requests` for `/start-chat` 200).
- Issue command names must match `^[a-z][a-z-]{1,24}$` (no digits). The demo name must match `^[a-z0-9][a-z0-9-]{1,30}$`.
- `data.py reset` uses `batch_writer(overwrite_by_pkeys=...)`; a delete and a put of the same key in one batch fails.
- After changing a prompt in ACXD, export or record the saved flow and update `acxd/<flow>.flow.json` in the repo, so
  the repo matches what is live.

- **Identity by one-time code (maple-health pattern):** `send_code` stores only an HMAC of the code in a session row
  keyed by the Connect contact id from `nlx_context` and delivers the code out of band (simulated inbox table, or SES);
  it never returns the code to the model and answers the same for unknown emails. `verify_code` checks tries and
  expiry. Every patient operation reads the verified patient from that session row, never from model input, so a chat
  can only act for whoever verified in it. Store the session id in a field the model cannot set (`_cid`, overwritten
  from `nlx_context` on every request).
- Pushing files through the GitHub connector: a literal `\uXXXX` escape in the file source can be decoded on the way
  (it arrived as the real character). Write such characters as `chr(0x2028)` and verify every push with `cmp`.
- The kit page CSS: `.mode { display: flex }` used to override `hidden`; the template now has
  `[hidden] { display: none !important; }`. Keep it if you restyle.
- Terraform downloads are blocked in the Claude sandbox, but `tofu` (OpenTofu) is installed: `tofu fmt -check` and
  `tofu -chdir=stacks/<s> init -backend=false && tofu -chdir=stacks/<s> validate` reproduce the accelerator CI.
  `validate` evaluates resources even at `count = 0`: guard optional file-backed locals with `try()`.

## 7. Driving the ACXD UI from the built-in browser
- The user must be logged in to Connect, and the browser pane must be visible: a minimized app window stops painting and
  the page stops reacting. Ask the user to restore it.
- Prefer `find` refs and `get_page_text` over coordinates. Screenshots can lag one action and are scaled (the viewport
  can be 875 px wide while the screenshot is 800): compute coordinates from `getBoundingClientRect()` × (screenshot
  width / `innerWidth`). Do not use viewport emulation in ACXD (typing stops registering).
- **File import:** the picker opens through a click on an `<input type=file>`. Hook it before clicking Import: override
  `HTMLInputElement.prototype.click` and `EventTarget.prototype.dispatchEvent` so that, when armed, they set `files`
  from a `DataTransfer` holding the JSON and dispatch `change`. Keep the JSON in a page variable and switch pages with
  `history.pushState` + `popstate` so the hook survives (a full navigation loses it).
- **Importing without picking files by hand:** load the JSON into the page with `fetch` from
  `raw.githubusercontent.com` (public repo; patch URLs in JS), install the file-input hook, then use a small
  `__clickText(label)` helper that clicks the last visible element with that exact text. Data request: open any data
  request you own (`?modal=/data-requests/<id>`) → Settings → arm → Import → **Import as new data request** → Import →
  Done. Flow: open a flow you own → Settings (gear, button text `SettingsSettings`) → Advanced → arm → Import →
  **Import as new flow** → Import. App: Applications → New application → name → Create application → Add flow →
  pick the flow (it becomes Welcome) → Save. Seven data requests + flow + app took ~5 minutes this way.
- Touchpoint launcher: the first click sometimes only starts the chat (`/start-chat` 200) without opening the window;
  click again.
- Prompt editors are ProseMirror (`.tiptap.ProseMirror`): click into them, then type, or use
  `document.execCommand('selectAll')` + `insertText`. Read them back with
  `[...ed.querySelectorAll('p')].map(p=>p.textContent).join('\n')`.
- **Save** is the top-right button. Confirm a save in the network list (`POST /acxd/api/conversationTrees/<flow>-Omni`
  or `/acxd/api/variables/<id>`) and read that response to check what was stored. Undo can silently remove an edge:
  check the flow after any accidental click, and reload without saving if unsure.
- Formats learned from saved responses:
  - flow export: `{flowId, nodes{uuid: node}, description, aiDescription, untrained, slotTypes, metadata{path,tags},
    mainLanguageCode, languageCodes, mirroring}`;
  - edges: `childNodes[{nodeId, name, conditions}]`, with status conditions
    `{"left":{"type":"node_status"},"operator":"eq","right":{"type":"constant","value":"continuation|timeout|failure|escalation"}}`;
  - Generative Journey exit N: `{"left":{"type":"system","name":"System.gjConditionIndex"},...,"value":N}`;
  - GJ tools: `{type:"dataRequest", dataRequest:{type:"custom", dataRequestId, payload:{}, params:{}, headers:{}},
    prompt, scopeTags:[]}`;
  - GJ data capture: `dataCapture:{data:[{required, type:"slot", name}], exitEnabled}`, with slots attached in flow
    Settings → Attached slots;
  - node message: `messages:[{body, type:"text", skipTranslation:false, metadata:{}, messageId}]`.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Deploy comment "Stopped before the command ran" | Request rejected (actor not in `ALLOWED_ACTORS`, bad name) or OIDC failed: check `CI_ROLE`, the repo id in the trust, branch `main` |
| "Could not assume role with OIDC" after re-running trust setup | The OIDC provider was deleted: re-run `setup-github-trust.sh` unchanged |
| Stack fails with `InvalidContactFlowException` | Run the throwaway-flow check (section 6). Seen so far: Agentic CX block missing Speech/AudioFiller config; `RoutingCriteria` on `UpdateContactRoutingBehavior` |
| Deploy says "No changes" after a template default changed | `cloudformation deploy` reused the old parameter value: pass it explicitly from `demo.json` |
| `set-acxd-keys.sh: No such file` in CloudShell | Not inside a clone of the repo: `git clone <repo> && cd <repo>` first |
| Page says chat not ready / 503 `chat_not_ready` | No app id in `demo.json` or no deployment key secret, so no chat flow: steps 3.4–3.6 |
| Chat stuck on "Thinking..." | No greeting before the Live Sync node |
| Tools fail with 401 | ACXD secret value differs from the stack's key secret, or the header is not `{name:NLX.Secret}` |
| Hand-off attributes empty | Data requests without "Send context", or no `contact_id` context variable, or the intake op is not reading `$.InitialContactId` |
| `demo: reset` "duplicate keys" | Old `data.py`; use `batch_writer(overwrite_by_pkeys=...)` |
| Kit page shows a Talk toggle on a chat-only demo | Old template without `[hidden] { display: none !important; }` |
| Accelerator `static` CI fails, everything else passes | Run `tofu fmt -check` / `tofu validate` per stack (see section 6) |
| Live workflow differs from its source copy (test fails) | Copy the source copy into `.github/workflows/` through the web editor |
| Page actions do nothing | Missing Live Sync API key in the secret, the action is not registered in `actions.js`, or the prompt does not name it |
