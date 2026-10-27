# InstaBounty Local AI Developer

Shadow includes a local development agent backed by the Ollama models already provisioned on the workstation.

The default roles are:

- `qwen2.5-coder:7b` — implementation agent
- `gemma3:4b` — code reviewer
- `mxbai-embed-large` — retained for later repository/architecture retrieval work

## Install or update the runner

From the Ansible controller:

```bash
ansible-playbook -i inventory.yml playbook.yml \
  --tags ai_developer \
  -e perform_final_reboot=false
```

The role validates that Ollama is reachable and that the developer/reviewer models are installed before installing the runner.

Open a new terminal after provisioning so the updated machine `PATH` is available.

## Run a development task

Use `ai-dev` and provide a repository root and a concrete development task:

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Add unit tests for the uncovered error path in signer.py"
```

The runner will:

1. require a clean Git working tree;
2. switch to the configured base branch (`main` by default);
3. create an `ai/...` feature branch;
4. ask the coding model to inspect and modify the repository;
5. allow the model to run repository validation through a constrained command list;
6. ask the reviewer model to review the resulting diff;
7. feed blocking review issues back to the coding model for another pass;
8. create a local commit only after reviewer approval.

The runner never pushes the branch and never creates or merges a pull request. GitHub remains the review/deployment boundary.

## Choose the branch name

The default branch name is generated from the task. To choose one explicitly:

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --branch feature/signer-coverage `
  --task "Add unit tests for the uncovered error path in signer.py"
```

AI work branches must start with `ai/` or `feature/`. `main` and `master` cannot be used as work branches.

## Leave the changes uncommitted

For early testing of the agent, use `--no-commit`:

```powershell
ai-dev `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Add unit tests for the uncovered error path in signer.py" `
  --no-commit
```

The reviewer must still approve the change, but the working tree is left uncommitted for manual inspection.

## Review an existing diff

The reviewer can be run independently:

```powershell
ai-review `
  --repo C:\Users\Shadow\instabounty\gateway `
  --task "Review the signer unit-test changes"
```

It returns a JSON review containing `approved`, `summary`, and `issues`.

## Safety boundaries

The runner is intentionally more restrictive than a general shell agent.

- File operations are confined to the selected repository.
- `.git` contents cannot be read or modified by the model.
- The model cannot change branches, push, merge, rebase, reset, or tag.
- Git mutation is performed by the runner itself.
- The model receives only an allow-listed set of development commands.
- Dependency-install/update subcommands are not exposed to the model.
- `uv`, npm and pnpm are executed with offline environment settings.
- Shell metacharacter expansion is avoided by executing commands as argument arrays rather than through `cmd.exe` or PowerShell.
- The runner does not read credentials or inject secrets into model prompts.
- A dirty working tree is rejected at startup to avoid overwriting manual work.

The command allow-list is a guardrail rather than an operating-system sandbox. Repository scripts invoked through trusted development tools can themselves execute code, so the agent should still be used only against trusted InstaBounty repositories.

## Current command families

The implementation agent may invoke:

- `uv run ...`
- `pytest ...`
- `ruff ...`
- `mypy ...`
- `coverage ...`
- `pnpm test`, `pnpm run`, `pnpm exec`, and `pnpm lint`
- `npm test` and `npm run`
- `forge test`, `forge build`, and `forge fmt`
- `solc ...`
- read-only Git commands such as `status`, `diff`, `log`, `show`, `grep`, `ls-files`, and `rev-parse`

Additional command families should be added deliberately when a repository actually needs them.

## Typical workflow

```text
You + ChatGPT
    |
    | define a small, testable task
    v
ai-dev on Shadow
    |
    +-- Qwen inspects/implements/tests
    |
    +-- Gemma reviews
    |      |
    |      +-- issues -> Qwen revision
    |
    v
local feature-branch commit
    |
    v
push + GitHub pull request
    |
    v
ChatGPT/manual review + CI
    |
    v
merge
```

The next evolution of the runner can add repository embeddings and cross-repository architecture context using `mxbai-embed-large`, but the first version intentionally keeps the execution path simple and inspectable.
