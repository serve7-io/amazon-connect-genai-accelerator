# Connect Phase 1: one instance, fully in CloudFormation

One stack (`template.yaml`) builds a complete small Amazon Connect setup that can be deleted and redeployed:

| Resource | Count | Notes |
|---|---|---|
| Instance | 1 | `CONNECT_MANAGED` users, **Contact Lens on**, **flow logs on**, inbound + outbound calls |
| Storage | 2 | Call recordings and chat transcripts in this stack's S3 bucket (Contact Lens output lands there too) |
| Hours of operation | 2 | Sales: Mon–Fri 09–17. Support: every day (so tests work any time). Time zone parameter, default `America/Edmonton` |
| Queues | 2 | Sales → Sales Hours, Support → Support Hours |
| Routing profile | 1 | Support (priority 1) and Sales (priority 2) on voice and chat; voice 1, chat 2 at a time |
| Security profile | 1 | `Phase1 Agent`: `BasicAgentAccess`, `OutboundCallAccess` |
| Agent users | 2 | `agent1`, `agent2` on the routing profile; initial passwords generated into Secrets Manager |
| Flows | 2 | `Sales Inbound`, `Support Inbound`: flow logging → **Set recording and analytics behavior (Contact Lens, voice + chat)** → greeting → set queue → check hours → transfer to queue |
| Phone number | 0–1 | Optional (`PHONE_COUNTRY=CA`); pointed at `Support Inbound` after deploy |

## The ARN problem, and how it is solved

A flow exported from another instance contains that instance's ARNs (queues, hours, prompts, Lambdas, other flows).
In this template the flow `Content` is `!Sub` text where every ARN is a reference, for example
`"QueueId": "${SalesQueue.QueueArn}"`. CloudFormation substitutes this deployment's own ARNs, so each stack wires to its
own resources and the same template can be deployed any number of times.

To bring in a flow built in the console of another instance:

```bash
aws connect describe-contact-flow --instance-id <source-instance-id> --contact-flow-id <flow-id> > exported.json
python3 templatize-flow.py exported.json --logical-id OrdersFlow --name "Orders Inbound" > orders-flow.yaml
```

`templatize-flow.py` finds every ARN (actions and console metadata), names each reference after the resource name in
the flow (`queue "Sales"` → `${QueueSales.QueueArn}`), lists which resources the template must contain, and **exits
with an error if any account id, instance id or ARN is still hard-coded**. Use `--map` to point an ARN at an existing
logical id or parameter instead. Paste the printed resource into `template.yaml`.

## Run it (CloudShell, admin session in the target account)

```bash
cd ~/acc && git pull && cd cloudformation/connect-phase1
./phase1.sh deploy       # ~5-10 min; ends with the wiring check
./phase1.sh redeploy     # delete everything, deploy again, report both times (repeatability test)
./phase1.sh delete       # tear down (instance included)
```

Options: `ALIAS=my-alias`, `STACK=my-stack`, `PHONE_COUNTRY=CA PHONE_PREFIX=+1403` (needs phone-number quota).

`./phase1.sh verify` checks the **live** instance, not the template: Contact Lens and flow logs on; storage → bucket;
each queue → its hours; routing profile → both queues on both channels; each agent → routing profile + security
profile; each flow ACTIVE, Contact Lens block present, and **every ARN in the flow belongs to this instance and this
stack**; phone number → Support Inbound. Exit code 1 if anything is wrong.

## Console checklist (what the manager asked to confirm by eye)

Log in: Connect console → instance `s7-phase1-…` → **Emergency access** (or the access URL printed by deploy).

- [ ] Instance settings → Data storage: recordings and chat transcripts point to the stack bucket; Contact Lens enabled; flow logs enabled
- [ ] Routing → Flows → `Sales Inbound`: the **Set working queue** block shows **Sales**; **Check hours of operation** shows **Sales Hours**; **Set recording and analytics behavior** has Contact Lens on for voice and chat
- [ ] `Support Inbound`: queue **Support**, hours **Support Hours**
- [ ] Routing → Queues: Sales uses Sales Hours, Support uses Support Hours
- [ ] Users → Routing profiles: `Sales and Support Agents` lists both queues for voice and chat
- [ ] Users → User management: `agent1` and `agent2` use that routing profile and the `Phase1 Agent` security profile
- [ ] Channels → Phone numbers (if claimed): the number's flow is `Support Inbound`
- [ ] Test chat (Test chat page, flow `Support Inbound`): greeting, then a contact waiting in Support; log in as `agent1` in the CCP (password: `./phase1.sh passwords`) and accept it
- [ ] After `./phase1.sh redeploy`: same checks pass on the new instance (new ARNs, same wiring)

## Things CloudFormation cannot do (found while building)

- **Phone number → flow:** `AWS::Connect::PhoneNumber` claims a number but has no flow property. `phase1.sh` calls
  `AssociatePhoneNumberContactFlow` after the stack is up.
- **Deleting the stack releases the phone number** for good; `phase1.sh delete` refuses unless `CONFIRM_RELEASE=yes`.
- **Instance quota:** an account allows a limited number of Connect instances, and AWS also limits how often instances
  are created and deleted. Deploy where there is room, and do not loop redeploys.
- Unlimited AI is on by default for new instances; no setting is needed.

Checkov findings skipped on purpose (bucket access logging, versioning, CMK for test passwords) are explained in the
template's `Metadata`. This is a test stack; the production design is the Terraform accelerator in this repository.
