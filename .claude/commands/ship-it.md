---
description: "Ship the current branch as a PR (worker-template project ship-it)"
argument-hint: "[--base <branch>] [--title <value>]"
allowed-tools: ["Bash", "Read"]
---

# Ship It (worker-template)

Project-level ship-it for `worker-template`. Runs after `/prep-pr` finishes its
self-review and quality gates. Covers push and PR creation only.

**This repo has NO CI.** Acceptance is local commands only (`uv run ruff check`,
`uv run mypy`, `uv run pytest`), which `/prep-pr` has already run and passed by
the time this command executes. State that explicitly in the PR body so the
reviewer knows green means "local gates passed", not a workflow run. Merging is
handled by the operator/orchestrator — do not merge and do not attempt
auto-merge.

**Arguments:** "$ARGUMENTS"

## Step 1: Parse arguments

Base defaults to `main`; override with `--base <branch>`. Extract `--title <value>`
into `EXPLICIT_TITLE` if provided.

## Step 2: Push the branch

```bash
BRANCH=$(git branch --show-current)
git push -u origin "$BRANCH"
```

If push fails (e.g. diverged), BLOCK — never force-push without explicit user
approval.

## Step 3: Create the PR

Draft the title from the branch's commits (or use `EXPLICIT_TITLE`). Body must:

- Reference the ticket: `Closes #<ticket>` (branch names are `dev/<ticket>`)
- Include a "Verification (no CI in this repo)" section listing the local gate
  commands and their results
- Summarize the change set and call out anything a reviewer would ask about
  (holdbacks added, floor bumps, follow-up tickets)
- End with the Claude Code attribution line

```bash
gh pr create --base "$BASE" --head "$BRANCH" --title "$TITLE" --body "$BODY"
```

## Step 4: Report

Print the PR URL. Do NOT merge — the merge decision belongs to the
operator/orchestrator, who re-runs the local gates on the branch if needed.
