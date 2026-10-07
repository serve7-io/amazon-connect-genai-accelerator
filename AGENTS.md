# Rules for AI coding agents
- Never put provider or backend blocks inside `modules/`.
- Never hard-code ARNs, account IDs or regions; use variables or remote-state outputs.
- Every IAM statement names explicit resources; `*` needs a comment saying why.
- Every Bedrock call: guardrailConfig (numbered version, trace disabled), explicit maxTokens, untrusted text in guardContent.
- Flow JSON is edited only through `${...}` template placeholders.
- Before committing: `python3 -m pytest -q lambdas/genai_gateway/tests`, `terraform validate` per stack, `cfn-lint bootstrap/bootstrap.yaml`.
