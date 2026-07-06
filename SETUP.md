# TDD Workflow Setup Instructions

Ported from the RoleKeeper orchestrator setup. Follow these steps in order.

---

## Step 0: Fill in project context

`.agent-context.md` and `.cursor/rules/tdd-worker.mdc` (Stack Reference table)
are placeholders. Before queueing real tasks, fill in:
- Project overview / architecture in `.agent-context.md`
- Actual package layout, lint/test tools if different from `ruff` + `pytest`

---

## Step 1: Install Python lint/test tools

```bash
pip install ruff pytest
```

Verify:
```bash
ruff check .
pytest
```

(Both will pass trivially on an empty project — that's expected until code exists.)

---

## Step 2: Install the TDD MCP server dependencies

```bash
cd tdd-mcp
npm install
```

Test it starts:
```bash
npx tsx src/index.ts
# Should start silently (it's a stdio server — no output means it's working)
# Ctrl+C to stop
```

---

## Step 3: Make scripts executable

```bash
chmod +x scripts/lint.sh scripts/test.sh
```

Test lint manually:
```bash
bash scripts/lint.sh
```

Test tests manually:
```bash
bash scripts/test.sh
```

Both must exit 0 cleanly before agents can use them.

---

## Step 4: Enable the MCP server in Cursor

In Cursor:
- Open Settings → MCP
- You should see `scoretopia-tdd` listed
- Enable it

---

## Step 5: Queue your first task

Write a task file following `docs/tasks/TASK_FORMAT.md`:

```bash
cp docs/tasks/TASK_FORMAT.md docs/tasks/ready/001-coder.md
# Edit 001-coder.md with your actual task
```

Then open Cursor Agent and say:
> "Call get_current_task_state and begin work."

Alternatively, open a **Planner** session first (invoke the `planner` rule) to
turn a rough idea into a well-formed task file before handing off to the Worker.

---

## Daily Workflow

1. Write task files into `docs/tasks/ready/`
2. Open Cursor Agent → "Call get_current_task_state and begin work"
3. Agent works the TDD loop autonomously
4. When a task completes, agent outputs git commands — run them
5. Open PR → review → merge

---

## Monitoring

| Directory | Meaning |
|---|---|
| `docs/tasks/ready/` | Queued, waiting for an agent |
| `docs/tasks/done/` | Completed successfully |
| `docs/tasks/error/` | Failed 3 times — needs human review |
| `docs/tasks/review/` | Blocked — audit failure, needs intervention |
