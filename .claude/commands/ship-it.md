---
description: "Ship the current branch as a PR (worker-template project ship-it)"
argument-hint: "[--base <branch>] [--title <value>]"
allowed-tools: ["Bash", "Read"]
---

# Ship It (worker-template)

Project-level ship-it for `worker-template`. Runs after `/prep-pr` finishes its
self-review and quality gates. Covers push, PR creation, and (when
`.claude/project-config.yaml` allows it) arming auto-merge.

**Until the CI-bootstrap ticket lands, this repo has NO CI and no branch
protection.** Acceptance is local commands only (`uv run ruff check`,
`uv run mypy`, `uv run pytest`), which `/prep-pr` has already run and passed by
the time this command executes. State that explicitly in the PR body so the
reviewer knows green means "local gates passed", not a workflow run. The config
pins `pr.auto_merge: false` until required checks exist on `main`.

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

## Step 4: Arm auto-merge (gated by project config)

Auto-merge is armed only when `.claude/project-config.yaml` allows it
(`pr.auto_merge` absent or `true`). A repo without branch protection must keep
`pr.auto_merge: false` there — on such a repo `gh pr merge --auto` merges
immediately instead of waiting for CI.

```bash
PR_NUMBER=$(gh pr view --json number -q .number)
REPO_ROOT=$(git rev-parse --show-toplevel)
if [ -f "$REPO_ROOT/.claude/scripts/prep_pr_finalize.py" ]; then
  AUTOMERGE_CHECK="$REPO_ROOT/.claude/scripts/prep_pr_finalize.py"
else
  AUTOMERGE_CHECK="$HOME/.claude/scripts/prep_pr_finalize.py"
fi
if "$AUTOMERGE_CHECK" check-automerge-allowed --repo-path "$REPO_ROOT"; then
  gh pr merge "$PR_NUMBER" --auto --squash
else
  GATE_STATUS=$?
  if [ "$GATE_STATUS" -ne 1 ]; then
    echo "Auto-merge gate failed unexpectedly (exit $GATE_STATUS) — BLOCK." >&2
    exit 1
  fi
  echo "Auto-merge disabled via .claude/project-config.yaml (pr.auto_merge: false) — leaving PR #$PR_NUMBER open for manual merge."
fi
```

If `check-automerge-allowed` permits arming and the `gh pr merge --auto` call
itself fails (for example the repo setting `allow_auto_merge` is off), BLOCK
with the `gh` error verbatim — the PR exists but auto-merge is not on; never
leave it silently unset. If the check reports disallowed, that is deliberate
repo state, not a failure — do not BLOCK.

## Step 5: Register PR monitor

```bash
PR_NUMBER=$(gh pr view --json number --jq .number)
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
REPO_PATH=$(git rev-parse --show-toplevel)
HEAD_SHA=$(gh pr view --json headRefOid --jq .headRefOid)

~/.claude/scripts/review_monitor.py register "$PR_NUMBER" \
  --role author \
  --repo "$REPO" \
  --repo-path "$REPO_PATH" \
  --sha "$HEAD_SHA"
```

## Step 6: Report

Print the PR URL, whether auto-merge was armed or deliberately left off, and
the CI status command (`gh pr checks <num>`). Never merge directly.
