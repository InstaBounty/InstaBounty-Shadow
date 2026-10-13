# InstaBounty Shadow

Infrastructure-as-code configuration for building the Windows development workstation used for the InstaBounty project.

The goal of this repository is to make a new Shadow development machine reproducible with as little manual configuration as possible.

The workstation itself runs Windows, while Ansible is executed from WSL on the local development machine.

## Architecture

```text
Local Windows PC
│
├── WSL2 / Ubuntu
│   ├── Git
│   └── Ansible
│
└── Tailscale
        │
        ▼
Shadow Windows workstation
    └── OpenSSH Server
```

Ansible connects to the Shadow machine over SSH through Tailscale.

## Repository Structure

```text
InstaBounty-Shadow/
├── .gitignore
├── README.md
├── inventory.yml
├── playbook.yml
├── requirements.yml
├── scripts/
│   └── setup-shadow.ps1
├── host_vars/
│   ├── shadow.yml
│   └── shadow.example.yml
└── roles/
```

`host_vars/shadow.yml` contains machine-specific or sensitive configuration and must not be committed.

## Clean Shadow VM Bootstrap

A newly reset Shadow VM needs a small amount of configuration before Ansible can connect to it. The bootstrap script automates the Windows-side prerequisites instead of requiring the OpenSSH, firewall and Tailscale steps to be performed manually.

The bootstrap deliberately remains separate from Ansible: its job is only to make a clean Windows VM reachable by the Ansible control machine.

### Run the bootstrap

On the Shadow VM, obtain this repository (or copy `scripts/setup-shadow.ps1` onto the VM), then open **Windows PowerShell as Administrator**.

From the repository root run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-shadow.ps1
```

The execution-policy change applies only to the current PowerShell process.

The script prompts for the Windows username Ansible should use and whether Tailscale should be installed automatically.

It then:

- checks and installs Windows OpenSSH Server if required
- enables and starts `sshd`
- configures the inbound TCP/22 firewall rule
- sets Windows PowerShell as the default OpenSSH shell
- installs Tailscale with `winget` when requested
- starts Tailscale and initiates authentication
- verifies that SSH is running and TCP/22 is listening
- detects the Tailscale IPv4 address when available
- prints the values required for `host_vars/shadow.yml`

The script is intended to be safe to rerun: existing OpenSSH, firewall and Tailscale configuration is reused rather than deliberately removed and recreated.

### Tailscale on the control PC

The local Windows PC / WSL Ansible controller must also be connected to the same Tailscale network. Install Tailscale on the control PC if it is not already present and sign in to the same tailnet.

### SSH key

The bootstrap does not copy private keys or credentials. Ansible authenticates using the dedicated private key configured in `inventory.yml`:

```yaml
ansible_ssh_private_key_file: ~/.ssh/instabounty_shadow
```

The corresponding public key must be installed for the Windows account on the Shadow VM before Ansible can use key-based authentication.

After bootstrap, create `host_vars/shadow.yml` from `host_vars/shadow.example.yml` and fill in the Tailscale IP, Windows username and PostgreSQL password.

## Software Installed

The Ansible configuration installs and configures the main InstaBounty development toolchain.

### Windows tooling

- PowerShell 7
- Windows Terminal
- 7-Zip
- Git for Windows
- GitHub CLI
- Visual Studio Code
- Postman

Postman is installed as the Windows desktop application for manual API testing during Gateway and service development.

### Web development

- Node.js LTS
- Corepack
- pnpm

Project-specific packages such as Next.js, React, TypeScript, React Native and Expo are installed within their individual projects rather than globally.

### Python

- Python 3.13
- uv

Python project dependencies should normally be managed using `uv`.

### Native build tooling

- Visual Studio Build Tools
- C/C++ build tools

### Smart contract development

- Foundry
  - Forge
  - Cast
  - Anvil
  - Chisel
- Solidity compiler

Hardhat is installed on a project-by-project basis rather than globally.

### Android development

- Eclipse Temurin JDK 21
- Android SDK
- Android platform tools
- adb
- Android build tools
- Android Studio

Local Android emulation is not required for the current workflow. Android applications can be built locally and tested on physical devices or services such as AWS Device Farm.

### Database

- PostgreSQL

### AI development

- NVIDIA GPU/driver verification
- Ollama
- Local LLM models
- Embedding model

The initial model configuration includes models for:

- coding
- general-purpose AI workloads
- semantic embeddings

### AWS and infrastructure

- AWS CLI v2
- Terraform

AWS resources used by InstaBounty can therefore be provisioned through Terraform rather than being created manually.

### Visual Studio Code Extensions

The workstation also installs the VS Code extensions used by the project, including support for:

- PowerShell
- YAML
- Ansible
- Git
- Python
- Pylance
- Ruff
- ESLint
- Prettier
- Tailwind CSS
- Solidity
- REST APIs
- Terraform
- AWS

## Ansible Requirements

Install the required Ansible collections:

```bash
ansible-galaxy collection install -r requirements.yml
```

The project currently uses:

```yaml
collections:
  - name: ansible.windows

  - name: community.windows
```

## Machine Configuration

Create:

```text
host_vars/shadow.yml
```

using:

```text
host_vars/shadow.example.yml
```

as a template.

Example:

```yaml
---
ansible_host: YOUR_TAILSCALE_IP
ansible_user: shadow

postgresql_superuser_password: "YOUR_PASSWORD"
```

Do not commit `host_vars/shadow.yml`.

## SSH

Ansible authenticates to the Shadow machine using a dedicated SSH key.

The inventory references the private key:

```yaml
ansible_ssh_private_key_file: ~/.ssh/instabounty_shadow
```

The corresponding public key must be installed on the Shadow Windows machine.

## Test Connectivity

Before provisioning the machine:

```bash
ansible all -i inventory.yml -m ansible.windows.win_ping
```

A successful connection should return:

```text
pong
```

## Provision the Workstation

Run:

```bash
ansible-playbook -i inventory.yml playbook.yml
```

The machine performs a final reboot after provisioning.

To run the playbook without the final reboot:

```bash
ansible-playbook -i inventory.yml playbook.yml \
  -e perform_final_reboot=false
```

Ansible reconnects to the Shadow workstation after reboots performed during provisioning.

### Install only Postman

The Postman role is tagged, so it can be run independently without provisioning the full workstation:

```bash
ansible-playbook -i inventory.yml playbook.yml --tags postman
```

Because the final reboot task is not tagged `postman`, this command only runs the Postman role and does not reboot the workstation afterward.

## Manual Configuration After Installation

Most workstation configuration is automated, but credentials and account authentication are deliberately kept outside the repository.

### GitHub CLI

Authenticate GitHub CLI:

```powershell
gh auth login
```

Verify:

```powershell
gh auth status
```

GitHub credentials must not be stored in this repository.

### AWS Authentication

The AWS CLI is installed automatically, but AWS authentication must be configured separately.

IAM Identity Center / SSO is preferred over permanent AWS access keys.

Configure an SSO profile:

```powershell
aws configure sso
```

Login:

```powershell
aws sso login
```

Verify the active identity:

```powershell
aws sts get-caller-identity
```

Terraform can use the same AWS profile/session.

AWS access keys must never be committed to this repository or placed directly inside Terraform files.

### Terraform

Verify Terraform:

```powershell
terraform version
```

Terraform infrastructure configuration belongs in the appropriate InstaBounty infrastructure repository rather than this workstation configuration repository.

### Ollama

Verify Ollama:

```powershell
ollama --version
```

List installed models:

```powershell
ollama list
```

The Ollama API is normally available at:

```text
http://127.0.0.1:11434
```

### PostgreSQL

PostgreSQL credentials are supplied through the ignored Ansible host configuration.

The PostgreSQL superuser password must therefore remain in:

```text
host_vars/shadow.yml
```

and must never be committed.

## Networking

The Shadow machine is accessed through Tailscale because direct inbound connectivity to the Shadow public IP is not relied upon.

Both the Ansible control machine and Shadow workstation must be connected to the same Tailscale network.

The following Windows services should start automatically:

- OpenSSH Server
- Tailscale

This allows Ansible to reconnect after Windows reboots.

## Docker

Docker Desktop is intentionally not installed.

The Shadow environment does not expose the nested virtualization capability required for Docker Desktop / WSL2 virtualisation.

The InstaBounty development stack therefore runs natively on Windows where possible.

Examples:

- Next.js → Node.js / pnpm
- Python → Python / uv
- Ethereum → Foundry / Anvil
- PostgreSQL → native Windows PostgreSQL
- AI models → Ollama
- Android → JDK / Android SDK

A remote Linux Docker host can be introduced later if containerized workloads become necessary.

## Security

Do not commit:

- SSH private keys
- AWS credentials
- PostgreSQL passwords
- API keys
- authentication tokens
- machine-specific secrets

Secrets and credentials should remain outside the repository.

## Development Philosophy

The repository configures the development machine.

Individual application repositories are responsible for their own project dependencies and versions.

This keeps the workstation reproducible while allowing each InstaBounty component to pin the exact versions it requires.

The intended separation is:

```text
Ansible
    → developer workstation configuration

Terraform
    → AWS infrastructure

Application repositories
    → application dependencies and source code
```
