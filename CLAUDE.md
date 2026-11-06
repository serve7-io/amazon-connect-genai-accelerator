# Notes for Claude

- Rules for any AI agent working in this repo: [AGENTS.md](AGENTS.md).
- A person starting from their own Claude, GitHub and AWS accounts: [docs/start-here-with-claude.md](docs/start-here-with-claude.md),
  then [docs/setup-new-org.md](docs/setup-new-org.md).
- Skills with the working rules and lessons learned: [claude/skills/](claude/skills/README.md)
  (`connect-accelerator-deployer` for the accelerator, `connect-acxd-demo-builder` for `demos/`).
- Never ask for, read or print secrets. Ask before deleting anything, spending money, or touching prod or a client account.
- Merges to `main` that touch `stacks/ modules/ flows/ lambdas/ tenants/ scripts/ bootstrap/` deploy the CI tenants.
  `docs/`, `demos/`, `claude/` and `tools/` do not (demos deploy only through `demo:` issues or auto-ship).
