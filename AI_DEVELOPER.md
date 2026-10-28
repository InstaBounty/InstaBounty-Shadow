# InstaBounty Local AI Developer

Shadow uses an Aider-backed local development workflow with Ollama models.

The default roles are:

- `qwen3-coder:30b` — implementation model used by Aider
- `gemma3:4b` — independent diff reviewer
- `mxbai-embed-large` — retained for later repository/architecture retrieval work

## Install or update

From the Ansible controller:

```bash
ansible-playbook -i inventory.yml playbook.yml \
  --tags llm_models,ai_developer \
  -e perform_final_reboot=false
```

The roles verify Ollama, provision the required local models, install `aider-chat` with `uv tool install`, and install the `ai-dev` / `ai-review` wrappers under `C:\Program Files\InstaBounty\bin`.

## Run a development task

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Add a GET /ping endpoint"
```

The runner will:

1. require a clean Git working tree;
2. switch to `main` (or the configured base branch);
3. create an `ai/...` or `feature/...` work branch, or reuse an explicitly supplied existing PR branch;
4. run Aider non-interactively with `ollama_chat/qwen3-coder:30b`;
5. use Aider's `whole` edit format for more reliable local-model edit application;
6. auto-detect a conservative repository validation command when possible (`uv run pytest`, `pnpm test`, `npm test`, or `forge test`) and give it to Aider's auto-test loop;
7. enforce a deterministic quality gate that rejects housekeeping-only diffs before model review;
8. send the resulting diff, changed-file list and repository context to Gemma for independent review;
9. if the quality gate or Gemma rejects the diff, run one Aider revision pass with the review feedback;
10. create a local commit only after approval.

Aider auto-commits and commit attribution are disabled. The wrapper owns the final commit. Nothing is pushed and no pull request is created by Shadow itself.

## Custom validation

Override the detected validation command when a repository needs something more specific:

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Update request tracing" `
  --test-cmd "uv run pytest tests/unit"
```

## Choose or reuse a branch

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --branch feature/ping-endpoint `
  --task "Add a GET /ping endpoint"
```

AI work branches must start with `ai/` or `feature/`. `main` and `master` cannot be used as work branches. Gina supplies the existing PR branch when continuing a matching task.

## Leave changes uncommitted

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Add a GET /ping endpoint" `
  --no-commit
```

Gemma must still approve the diff, but the changes remain uncommitted for inspection.

## Review an existing diff

```powershell
ai-review `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Review the current Gateway changes"
```

## Safety boundaries

- Aider runs with the selected repository as its working directory.
- The wrapper requires a clean repository before starting.
- Branch creation/reuse is performed by the wrapper, not by the model.
- Aider auto-commits are disabled.
- A deterministic quality gate rejects housekeeping-only changes for implementation tasks.
- Gemma reviews the actual Git diff before the wrapper commits.
- The runner does not push, create pull requests, or merge.
- Gina strips Slack/GitHub credentials before launching `ai-dev`.
- Gina owns push/PR handoff and failed `ai/...` branch cleanup.
- Aider analytics/update checks, URL detection, Playwright and fancy input are disabled for automated runs.
- Project test commands are run with offline package-manager environment settings where applicable.

Aider replaces the previous hand-written JSON action loop. It provides repository mapping, edit application, context management, and test-repair behaviour without requiring the local model to manually orchestrate `read_file`, `search_text`, `write_file`, and `run_command` actions.

## Workflow

```text
Slack
  |
  v
Gina
  |
  v
ai-dev on Shadow
  |
  +-- Aider + Qwen3-Coder implements/tests
  |
  +-- deterministic quality gate
  |
  +-- Gemma reviews diff
  |      |
  |      +-- issues -> Aider revision pass
  |
  v
reviewed local commit
  |
  v
Gina pushes + creates/updates GitHub PR
  |
  v
human review / CI / merge
```

The initial Aider integration deliberately used `qwen2.5-coder:7b` so framework failures could be separated from model capability. After repeated successful Aider runs that produced no meaningful repository edits, the implementation model was upgraded to `qwen3-coder:30b` while keeping Gina, Aider, Gemma and the quality gate unchanged.
