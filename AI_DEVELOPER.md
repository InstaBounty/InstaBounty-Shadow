# InstaBounty Local AI Developer

Shadow uses an Aider-backed local development workflow with Ollama models.

The default roles are:

- `qwen3-coder:30b` — implementation model used by Aider
- `gemma3:4b` — independent requirement/diff reviewer
- `mxbai-embed-large` — retained for later repository/architecture retrieval work

## Install or update

From the Ansible controller:

```bash
ansible-playbook -i inventory.yml playbook.yml \
  --tags ai_developer \
  -e perform_final_reboot=false
```

The tagged workflow also runs the `llm_models` dependency first, so the required Ollama models are provisioned before the AI developer is installed.

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
6. for Python repositories, run unit tests with `pytest-cov` and require 100% code coverage;
7. for non-Python repositories, use the repository's normal detected test command (`pnpm test`, `npm test`, or `forge test`) until a repository-specific coverage command is configured;
8. reject housekeeping-only diffs before model review;
9. reject component-test changes unless the original task explicitly requests component tests;
10. reject missing unit-test changes when the task explicitly asks for unit tests;
11. send the resulting diff, changed-file list, repository context and coverage evidence to Gemma;
12. require Gemma to verify every explicit task requirement individually with concrete evidence;
13. if the deterministic gate or Gemma rejects the diff, run one Aider revision pass with the exact blocking issues;
14. create a local commit only after every quality gate passes.

Aider auto-commits and commit attribution are disabled. The wrapper owns the final commit. Nothing is pushed and no pull request is created by Shadow itself.

## Unit-test coverage policy

Python production-code changes must maintain **100% unit-test code coverage**.

The default Python validation command is equivalent to:

```powershell
uv run --with pytest-cov pytest tests/unit `
  --cov=<package> `
  --cov-report=term-missing `
  --cov-report=json:coverage.json `
  --cov-fail-under=100
```

If a repository does not have `tests/unit`, its normal `tests` directory is used instead. The quality gate independently reads `coverage.json`; a successful test command alone is not sufficient evidence.

Coverage applies to unit testing by default. **Component tests are only created or modified when the original task explicitly requests component tests.**

## Requirement-verification gate

The reviewer receives:

- the original task;
- the exact changed-file list;
- the repository file map;
- the proposed Git diff;
- measured coverage evidence.

Gemma must decompose the task into its explicit requirements and return a result for each requirement containing the requirement text, whether it is satisfied, and concrete evidence. The runner rejects the review if requirement-by-requirement verification is missing or any requirement is unsatisfied.

For API changes this includes exact response/request names, values, types, headers, status codes, routing/OpenAPI wiring and other behaviour stated in the prompt. Similar-but-different behaviour does not satisfy the gate.

## Custom validation

Override the detected validation command when a repository needs something more specific:

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Update request tracing" `
  --test-cmd "uv run --with pytest-cov pytest tests/unit --cov=gateway --cov-report=term-missing --cov-report=json:coverage.json --cov-fail-under=100"
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

All quality gates must still pass, but the changes remain uncommitted for inspection.

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
- Housekeeping-only changes cannot satisfy implementation work.
- Python production-code changes cannot pass below 100% unit-test coverage.
- Component tests are outside the default coding scope unless explicitly requested.
- Gemma must verify each explicit requirement rather than issuing a broad approval summary.
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
  +-- Aider + Qwen3-Coder implements
  |
  +-- unit tests + 100% coverage gate
  |
  +-- deterministic scope/completeness gates
  |
  +-- Gemma requirement-by-requirement review
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

The initial Aider integration deliberately used `qwen2.5-coder:7b` so framework failures could be separated from model capability. After repeated successful Aider runs that produced no meaningful repository edits, the implementation model was upgraded to `qwen3-coder:30b` while keeping Gina, Aider, Gemma and the surrounding orchestration unchanged.
