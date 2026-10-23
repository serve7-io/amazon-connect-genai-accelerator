# CAA Scenario 1: persistent cross-channel roadside assistance (demo)

Built to Vanja's "CCG Scenario 1 build brief" (15 Oct 2026). Synthetic data only.

Story: Sarah Mitchell (Plus, M-20481537, 2023 Honda CR-V, ON ABCD 123) has a flat tire with no spare on Highway 401 eastbound near Joyceville Road, Kingston, on a Sunday. She goes from AI web chat, to a live chat with James Rivera, to a voice call, and keeps her context the whole way.

## What the stack builds (`demo-ccg-s1`)

The stack is named `demo-*` because the CI role may only create IAM roles with that prefix. All other names follow the brief.

**Tables (seeded from `lambdas/api/seed.json`):**
- `ccg-s1-members`, with GSI `phone-index` on `phone_key` (the last 10 digits);
- `ccg-s1-service-history`;
- `ccg-s1-providers` (Kingston Auto Services plus 2 decoys);
- `ccg-s1-dispatches`;
- `ccg-s1-service-records` (mock ServiceNow);
- `ccg-s1-intake` (hand-off record per chat contact, 7-day TTL).

**Lambda `ccg-s1-api`:**
- Function URL `POST /<op>`, with the header `x-api-key`.
- Connect invoke reads `Details.Parameters.op` and returns a flat string map (`$.External.<key>`).
- `GET /` serves the member portal page.
- Operations: `lookupmember`, `verifyservice`, `coverage`, `dispatch`, `writeback`, `reset`, `intake`.
- **Hand-off (16 Oct):** when ACXD calls `lookupmember`, `verifyservice` or `coverage`, the Lambda takes the contact id from ACXD `nlx_context` (never from the model) and merges the successful result into `ccg-s1-intake`. After the escalation, the Connect flow invokes `intake` and sets the `s1_*` attributes from it. This is the same server-side pattern as Scenario 5. It replaces the ACXD state modifications, which cannot use tool results ("placeholders no longer supported" on the Escalate node).
- Every response has `message`. Errors also have `error`, plus `valid_options` where useful.
- `samples/` holds a success and an error sample for each operation. Regenerate them with `python3 tests/make_samples.py`.

**Connect:**
- hours `ccg-s1 Roadside 24x7`;
- queue `Roadside`;
- predefined attribute `roadside_dispatch` (value `certified`);
- routing profile `Roadside Agents`: chat 2 and voice 1, cross-channel (ROUTE_ANY_CHANNEL);
- quick connect to Sarah's demo mobile, once `SarahDemoPhone` is set.

**Overflow:**
- Today any agent with the Roadside queue takes the chat. The `roadside_dispatch` attribute and `set-proficiency` exist.
- Proficiency-first routing (level 4+, then anyone after 90 s) is not in the flow yet. Connect rejects
  `UpdateContactRoutingBehavior` with `RoutingCriteria` in flow JSON (issues #79, #81). Next step: build the
  "Set routing criteria" block once in the console, export the flow and copy its exact JSON shape.
- Overflow to a separate general queue is not built. It would need a customer queue flow, or adding Roadside to the Basic routing profile.

**Chat flow `ccg-s1-chat-main`.** It is created only when `AcxdApplicationId` and `AcxdAlias` are set:
1. Logging on, then the guide hook (DefaultAgentUI).
2. Greeting, then the Agentic CX block (`contact_id` = `$.InitialContactId`, `channel` = `CHAT`).
3. Escalation: `intake` (Lambda) → `s1_*` attributes → `Roadside` queue.
4. Error: apology, then on to Roadside anyway.
5. Idle: closing message.
6. Done: goodbye, then disconnect.

## Operate (issue titles)

- `demo: deploy ccg-s1`
- `demo: status ccg-s1`
- `demo: reset ccg-s1` (all tables back to the seed)
- `demo: test ccg-s1`
- `demo: connect-info ccg-s1` (read-only)
- `demo: flow-snapshot ccg-s1` (read-only, Scenario 5 flows)
- `demo: set-proficiency ccg-s1` (after James exists)

## Console steps (Milos)

1. **Attachments.** Data storage > Attachments > Enable:
   - bucket `s7-connect-phase1-storagebucket-yjfuv3mn3boh`, prefix `connect/serve7-ccg-demo-222980/Attachments`;
   - chat file types jpg, jpeg, png and heic, up to 10 MB;
   - S3 CORS for PUT and GET;
   - a 1-day lifecycle on the staging prefix only.
2. **Agent user.** Create user `james.rivera`: security profile Agent; he sets his own password. Then run `demo: set-proficiency ccg-s1`.
3. **Contact Lens.** Turn on Contact Lens generative post-contact summaries, if they are not on already.

The ACXD data requests use the existing ACXD secret `ccg_s5_api_key` (the same key), so no secret step is needed.

## ACXD checklist (Vanja), in build order

**Resources:**
- Data requests (named `s1lookupmember`, `s1verifyservice`, `s1coverage`, because S5 already has `lookupmember`): External, POST, URL = stack output `AcxdApiUrl` + `/<op>`, header `x-api-key: {ccg_s5_api_key}`:
  - `lookupmember`: request `member_id` ("membership number exactly as given, e.g. M-20481537") or `phone` ("phone number on the account, digits only").
  - `verifyservice`: request `member_id`, `vehicle_confirmed` ("the vehicle the member confirmed, or yes"), `interaction_type` ("always service").
  - `coverage`: request `member_id`, `service` ("one of tow, tire, boost, lockout, fuel").
  - On all three: response models generated from `samples/<op>.json`, using both shapes. Interim message: "One moment while I check that."
- Context variables, with Allow frontend updates on: `contact_id` (required: it is how the Lambda ties tool calls to the chat) and `channel`.
- Guardrails:
  - input: prompt injection only;
  - output: "claims dispatch is confirmed", set to Flag.

**Flow `ccgroadsideintake`:**
- Start → Generative Journey "Roadside intake (chat)" → exits.
- Prompt order:
  1. safety first;
  2. membership number or phone;
  3. `lookupmember`;
  4. confirm the vehicle on file;
  5. `verifyservice`;
  6. location (free text or landmark);
  7. invite a photo with the paperclip and acknowledge it in general terms;
  8. spare or tow;
  9. `coverage`;
  10. offer a person.
- Rules:
  - call each tool again whenever the member changes a value;
  - never state a value a tool did not return;
  - never claim to see the photo;
  - never confirm a dispatch (only the agent dispatches);
  - exits are silent;
  - no em dashes, short sentences, Canadian spelling.
- Exit `escalate_agent` → Escalate node. Message: "I'm connecting you with a roadside agent now. They'll have everything from our conversation." No state modifications are needed.
- **Correction to the brief:** the brief's query-string Node payload (`auth={authStatus}&...`) is not an ACXD mechanism, and Escalate-node state modifications cannot use tool results. The values reach Connect through the server-side intake record instead (see Hand-off above). The hand-off summary is built by `coverage` from checked data, not by a generative text node.
- Exit `done` → Exit application. Leave the Exit node message empty; the Connect flow says goodbye.
- Wire every Failure and Timeout edge to a recovery Basic node that escalates.

**App `ccg-roadside-intake`:**
- Welcome, Fallback and Escalation default behaviours → `ccgroadsideintake`. Do not use Build anyway.
- Build and deploy to Production. Send Claude the app id and the Production deployment key.

## Web chat (ACXD Touchpoint)

Milos chose ACXD's own chat (16 Oct), so quick replies, cards and other modalities can render.

- `GET /` on the API URL is the member portal page. It loads `@amazon-connect-touchpoint/web` (pinned to 1.0.3, from jsDelivr) as a floating chat.
- `POST /start-chat` is the browser endpoint Touchpoint calls. It runs Connect `StartChatContact` with the instance and the flow `ccg-s1-chat-main` fixed server-side; only the display name comes from the browser. It returns 503 `chat_not_ready` until the flow exists.
- Plain chat needs no ACXD API key. That key is only for Live Sync, which this demo does not use.
- The chat is a normal Connect chat contact, so the escalation to James in the Roadside queue works as with any chat.

## Phase B and C (next)

- **Guide flow `ccg-s1-guide-roadside`:**
  - `lookupmember` → S1 view (member card, Verified and Standard badges, pre-filled fields, shop dropdown defaulting to "Nearest certified facility", Initiate Dispatch) → `dispatch` → `s1_dispatch_*`;
  - Send message SMS, only when `SmsEnabled` = true and an SMS number exists;
  - hooked from the chat flow with Set event flow (Default flow for Agent UI).
- **ACW:** start from the AWS default ACW guide, then `writeback`.
- **Chat to voice:** James dials from the CCP phone tab. This is a new contact linked by `PreviousContactId`.

## Open items (from the brief)

1. Touchpoint chat to live agent: expected to work, because Touchpoint runs on a normal Connect chat contact (StartChatContact + ChatJS). Confirm in the first test chat.
2. Does Touchpoint support attachments?
3. Are generative post-contact summaries produced for chat?
4. Do attributes reach the linked outbound call?
5. Is there an SMS-enabled number?
6. CCG facts to confirm: 200 km Plus tow, and the Sunday shop hours (open until 5 PM per the script).

Also: Contact Lens real-time analytics for chat (add it in the console, or in the flow once its JSON shape is confirmed).

## Assumptions and invented values

- **Seed data:**
  - Sarah's phone (+1 613 555 0152), postal code K7L 3N6 and date of birth 1986-04-22 are invented (`demo_value_fields`);
  - the decoy members and decoy providers carry `demo_value: true`;
  - Basic 10 km and Premier 320 km coverage carry `demo_value: true`. Plus 200 km comes from the script.
- **Overflow:** 90 s, then any Roadside agent.
- **API key:** the same key as Scenario 5, used in ACXD as the existing secret `ccg_s5_api_key`.
- **Stack name:** `demo-ccg-s1` (CI IAM limit), not `ccg-s1`.
- **Sarah in Scenario 5's table:** Sarah's row in `ccg-s5-members` from 14 Oct is no longer used. The Scenario 5 code changes from #21 were reverted in #34 and never deployed.
