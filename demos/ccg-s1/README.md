# CAA Scenario 1: persistent cross-channel roadside assistance (demo)

Dan's script, Part 1: Sarah Mitchell, a Plus member (M-20481537, 2023 Honda CR-V), has a flat tire with no spare on Hwy 401 near Kingston. **Synthetic data only.**

## Phases
- **A (this stack):**
  - member-portal page with the Connect chat widget;
  - ACXD roadside intake agent: safety check, shared CRM lookup by membership number or phone, service-level verification by vehicle, location, photo attachment, tow coverage;
  - hand-off to the **Roadside Assistance** queue. The AI intake is shown to the agent as contact attributes (`s1_*`), alongside the full chat transcript.
- **B:**
  - agent workspace Step-by-Step Guide (intake view with the verified badge and dispatch button);
  - dispatch Lambda (simulated provider, ETA);
  - ACW guide.
- **C:**
  - chat-to-voice to the member's phone (same interaction);
  - SMS with driver and ETA;
  - simulated ServiceNow write-back;
  - dashboards (later).

## Pieces
- **Stack `demo-ccg-s1`** (`template.yaml`):
  - Cases table;
  - one Lambda with a function URL:
    - `POST /get_coverage`, `/create_roadside_case`, `/get_case` (x-api-key);
    - `GET /` member portal page;
    - Connect invoke returns the `s1_*` attributes;
  - Connect: `ccg-s1 Roadside 24x7` hours, `Roadside Assistance` queue, `Roadside Agents` routing profile (chat 2 + voice 1);
  - flow `ccg-s1-chat`, created once `AcxdApplicationId` and `AcxdDeploymentKey` are set.
- **Shared with Scenario 5 (`demos/ccg-s5`):** the CRM lookup (`lookupmember`, now by membership number or phone) and authentication (`verifymember`, level `service` = membership number + vehicle). Sarah is in `ccg-s5/data/synthetic.json`.
- **ACXD:**
  - flow `ccgroadside` (`acxd/ccg-roadside.flow.json`);
  - new data requests `getcoverage` and `createroadsidecase`;
  - reused `lookupmember` (updated schema) and `verifymember`.

## Operate
Run these as issue titles:
- `demo: deploy ccg-s1`
- `demo: status ccg-s1`
- `demo: connect-info ccg-s1` (read-only instance summary)
- `demo: test ccg-s1`

## One-time console step
Enable chat attachments: Connect console > the instance > Data storage > Attachments > Edit > Enable, using bucket
`s7-connect-phase1-storagebucket-yjfuv3mn3boh` and prefix `connect/serve7-ccg-demo-222980/Attachments`. This is a console step because
Connect updates its service-linked role, which CI may not do.

## Demo agent
User `james.rivera` is created in the Connect console by Milos (he sets the password himself), with:
- routing profile **Roadside Agents**;
- security profile **Agent**.
