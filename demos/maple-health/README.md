# Maple Health Network: patient web chat (demo)

A fictional Canadian clinic network (Ottawa Centre, Kanata, Gatineau). Synthetic data only: the organization, clinics,
providers, patients, emails (`@example.com`) and phone numbers (`555`) are invented.

The patient chats on a web page (ACXD Touchpoint, through Amazon Connect chat). The AI assistant:
- answers clinic questions (hours, addresses, services, languages) and shows open appointment times, without verification;
- verifies identity with a **one-time code sent by email** (simulated inbox on the page);
- after verification, lists, books, reschedules and cancels the patient's appointments;
- speaks English or French (Canadian French), following the patient;
- hands the chat to a **live agent** in the queue `Maple Health Patient Support` when asked, with a guide card
  (patient, verified or not, next appointment, what happened in the chat).

## How the email code works

- `send_code` (data request `mhsendcode`) looks up the email, creates a 6-digit code, stores only a keyed hash in the
  session record, and "emails" the code to the simulated inbox (`InboxTable`). The answer is the same whether or not the
  email is on file, and **the code never goes back to the model**.
- `verify_code` checks it: 10 minutes, 5 tries, 3 codes per chat.
- The session is the Connect contact id that ACXD sends in `nlx_context` (context variable `contact_id`). It is never
  taken from the model, so a chat can only act for the patient verified in that same chat.
- The page polls `GET <API>/inbox` (public, `@example.com` addresses only, last 30 minutes) and shows the emails.
  A real build would send the email with Amazon SES instead (same code path: `_deliver`).

## Hand-off to a person

On the ACXD escalation the Connect flow invokes the Lambda (`op=intake`, `contact_id=$.InitialContactId`), sets
`mh_verified`, `mh_patient_name`, `mh_patient_id`, `mh_language`, `mh_next_appointment`, `mh_summary`, and transfers to
`Maple Health Patient Support`. The guide flow `maple-health-guide` shows those to the agent.

To take the chat, an agent needs the routing profile `Maple Health Agents`: `demo: set-agent maple-health <username>`
(it prints the previous profile and how to switch back).

## Demo patients

| Patient | Email | Notes |
|---|---|---|
| Daniel Okafor | daniel.okafor@example.com | English, flu vaccine booked (MH-20417) |
| Amélie Tremblay | amelie.tremblay@example.com | French, Gatineau, no appointments |
| Priya Sharma | priya.sharma@example.com | English, physiotherapy booked in Kanata (MH-20418) |

## Operate (issue titles)

- `demo: deploy maple-health`, `demo: status maple-health`, `demo: reset maple-health`, `demo: test maple-health`
- `demo: inbox maple-health` (the simulated inbox)
- `demo: set-agent maple-health <username> [routing profile]`

## Build steps (see the skill connect-acxd-demo-builder)

1. Deploy once (API, tables, queue, routing profile, guide flow).
2. ACXD: import the 7 data requests (`acxd/datarequests/`, URLs from the deploy comment), the flow
   `mhpatientassistant`, and create the app `maple-health-patient-chat` (Welcome = the flow). Build & deploy (Milos).
3. CloudShell: `bash demos/_kit/web/set-acxd-keys.sh maple-health` (Production deployment key; no Live Sync key needed).
4. `demo.json`: add `web.acxd.application_id` and `web.page.inbox_url` (deploy output `InboxUrl`), deploy again.
5. Test: clinic question, verify with an email code, book / move / cancel, ask for a person, then `demo: reset`.

## Invented values

Everything. Health811 / 811 are mentioned by the assistant as public health lines; nothing else refers to a real service.
