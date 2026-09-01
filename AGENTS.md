<!-- gjalla-start -->
## gjalla — your local engineering context

This workspace uses gjalla in **local mode**: your personal rules, memories, and
skills live on your machine (`~/.gjalla/user/`) and are shared across every
agent, in any folder — no account or project required.

### Use it at session start
- `gjalla rules show` — the constraints and guidelines to follow here.
- `gjalla memory show` — durable, non-obvious facts worth knowing before you start.
- `gjalla skills show` — available skills; `gjalla skills show <slug>` for one in full.
- `gjalla docs show` — unstructured reference docs; `gjalla docs show <name>` for one in full.

Ground your work in this context and keep changes aligned with it.

### Save what you learn
When you uncover a durable, non-obvious fact, save it so the next agent inherits it:
`gjalla memory add "<the fact>" -n "<short name>"`. Add a rule with
`gjalla rules add -n "<name>" -d "<what>"`. Both write to your personal context and
every agent picks them up.

### Connect later
Signing in and connecting a project syncs this local context up to your team and
turns on gjalla's full architecture map, review, and attestation. Until then,
everything above works entirely offline.

### Task Status
At the end of every turn, record your task status: `gjalla session status done|needs-human|needs-another-agent`. Use `--note "<text>"` to say what you need the human or other agent for.
<!-- gjalla-end -->
