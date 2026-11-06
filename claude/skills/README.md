# Claude skills for this repo

These skills teach Claude the working rules and the lessons learned for this repo. Upload them to your own Claude
account before you start (see [docs/start-here-with-claude.md](../../docs/start-here-with-claude.md)).

| Skill | Use it for |
|---|---|
| [`connect-accelerator-deployer`](connect-accelerator-deployer/SKILL.md) | The accelerator: new org/account, tenants, Terraform layers, CI/CD, AI agent, teardown |
| [`connect-acxd-demo-builder`](connect-acxd-demo-builder/SKILL.md) | The demo kit in `demos/`: ACXD client demos with chat, voice, Live Sync and live-agent hand-off |

## Install

1. Make one zip per skill, with the folder at the top of the zip:

   ```bash
   cd claude/skills
   zip -r connect-accelerator-deployer.zip connect-accelerator-deployer
   zip -r connect-acxd-demo-builder.zip connect-acxd-demo-builder
   ```

2. In the Claude app: Settings → Skills (in some versions Customize → Skills) → upload each zip, and turn it on.
3. Check: start a new chat and ask "Which skills do you have for the Connect accelerator?"

## Update

When a PR changes a `SKILL.md` here, re-zip it and upload it again; the upload replaces the old version. Claude also
warns you when the copy in the repo is newer than the one it is running.

Changes to these files never deploy anything (`claude/**` is outside the deploy paths).
