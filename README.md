# InstaBounty-Shadow

Infrastructure and workstation configuration for InstaBounty development machines running on Shadow PC.

The goal of this repository is to make a Shadow development workstation reproducible. A fresh Windows Shadow machine should require only a small bootstrap process before Ansible can configure the rest of the environment automatically.

## Architecture

The development environment is managed from a local Windows machine using WSL2 and Ansible.

```text
Windows Development PC
│
├── Visual Studio Code
│
└── WSL2 Ubuntu
    │
    ├── Git
    ├── Ansible
    ├── SSH
    └── InstaBounty-Shadow
          │
          │ SSH over Tailscale
          ▼
    Shadow Windows PC
```

Tailscale provides the private network connection between the local development machine and the Shadow PC.

Ansible connects to the Shadow PC using Windows OpenSSH and executes Windows configuration through PowerShell.

## Repository Structure

```text
InstaBounty-Shadow/
├── .gitignore
├── README.md
├── inventory.yml
├── playbook.yml
├── requirements.yml
│
└── host_vars/
    ├── shadow.example.yml
    └── shadow.yml
```

`shadow.yml` contains machine-specific configuration and is excluded from Git.

`shadow.example.yml` provides a template for creating the local configuration.

## Prerequisites

### Local machine

The Ansible controller runs inside Ubuntu on WSL2.

Required software:

- Windows Subsystem for Linux 2
- Ubuntu
- Git
- Ansible
- Visual Studio Code
- Visual Studio Code WSL extension
- Tailscale

### Shadow machine

The Shadow Windows machine requires:

- Tailscale
- Windows OpenSSH Server
- PowerShell
- SSH public-key authentication

These are currently considered bootstrap dependencies because Ansible cannot configure the machine until remote connectivity exists.

## Clone the Repository

From Ubuntu in WSL:

```bash
git clone git@github.com:<github-user>/InstaBounty-Shadow.git
cd InstaBounty-Shadow
```

The repository can also be opened directly in Visual Studio Code:

```bash
code .
```

VS Code should show:

```text
WSL: Ubuntu
```

in the bottom-left corner.

## Install Ansible

Install the required Python tooling:

```bash
sudo apt update
sudo apt install -y python3 python3-pip pipx
```

Add `pipx` applications to the shell path:

```bash
pipx ensurepath
exec $SHELL
```

Install Ansible:

```bash
pipx install --include-deps ansible
```

Verify the installation:

```bash
ansible --version
```

## Install Ansible Collections

Required collections are defined in:

```text
requirements.yml
```

Install them with:

```bash
ansible-galaxy collection install -r requirements.yml
```

Current requirements:

```yaml
---
collections:
  - name: ansible.windows
```

## SSH Key

A dedicated SSH key is used for managing the Shadow machine.

Create it from WSL:

```bash
ssh-keygen -t ed25519 \
  -f ~/.ssh/instabounty_shadow \
  -C "InstaBounty Shadow"
```

This creates:

```text
~/.ssh/instabounty_shadow
~/.ssh/instabounty_shadow.pub
```

The private key must remain on the Ansible controller.

Display the public key with:

```bash
cat ~/.ssh/instabounty_shadow.pub
```

The public key must be added to the Shadow Windows OpenSSH configuration.

Test SSH before using Ansible:

```bash
ssh -i ~/.ssh/instabounty_shadow shadow@<TAILSCALE_IP>
```

The connection should succeed without requesting the Windows account password.

## Machine Configuration

Machine-specific values are stored in:

```text
host_vars/shadow.yml
```

This file is excluded from Git.

Create it from the example:

```bash
cp host_vars/shadow.example.yml host_vars/shadow.yml
```

Then edit it:

```yaml
---
ansible_host: YOUR_TAILSCALE_IP
ansible_user: YOUR_WINDOWS_USERNAME
```

For example:

```yaml
---
ansible_host: 100.x.x.x
ansible_user: shadow
```

Do not commit `host_vars/shadow.yml`.

The committed example file should remain:

```text
host_vars/shadow.example.yml
```

## Inventory

The committed `inventory.yml` defines how Ansible connects to the Shadow host:

```yaml
---
all:
  hosts:
    shadow:
      ansible_connection: ssh
      ansible_shell_type: powershell
      ansible_ssh_private_key_file: ~/.ssh/instabounty_shadow
```

Ansible automatically loads the values from:

```text
host_vars/shadow.yml
```

for the `shadow` host.

## Test Ansible Connectivity

The initial playbook verifies that Ansible can communicate with Windows:

```yaml
---
- name: Verify InstaBounty Shadow workstation
  hosts: shadow
  gather_facts: false

  tasks:
    - name: Test Windows connectivity
      ansible.windows.win_ping:
```

Run it with:

```bash
ansible-playbook -i inventory.yml playbook.yml
```

A successful result should look similar to:

```text
TASK [Test Windows connectivity]
ok: [shadow]

PLAY RECAP
shadow : ok=1 changed=0 unreachable=0 failed=0
```

`win_ping` is not a normal ICMP ping. It verifies that Ansible can connect to Windows and successfully execute an Ansible Windows module.

## Network

The Shadow machine is accessed over Tailscale rather than its public internet address.

```text
Local Windows PC
       │
       │ Tailscale
       ▼
100.x.x.x private network
       │
       │ SSH :22
       ▼
Shadow Windows PC
```

Using Tailscale avoids exposing the Windows SSH service directly to the public internet.

## Current Bootstrap Process

A fresh Shadow machine currently requires the following manual bootstrap steps:

1. Install and connect Tailscale.
2. Install Windows OpenSSH Server.
3. Start and enable the `sshd` service.
4. Configure the Windows Firewall for OpenSSH.
5. Add the Ansible controller's SSH public key.
6. Verify SSH connectivity.
7. Run the Ansible playbook.

The long-term goal is to reduce these steps to a small reusable PowerShell bootstrap script.

## Project Goal

The eventual objective is for Ansible to configure the complete InstaBounty development workstation, including:

- Windows base configuration
- Remote desktop software
- Git
- Visual Studio Code
- Python
- Node.js
- Docker
- development tools
- environment configuration
- source repositories
- InstaBounty-specific development dependencies

A fresh Shadow machine should ultimately require only:

```text
Bootstrap connectivity
        ↓
Run Ansible
        ↓
Development workstation ready
```

## Security

Do not commit:

- SSH private keys
- passwords
- authentication tokens
- Tailscale credentials
- machine-specific secrets
- `host_vars/shadow.yml`

The SSH private key remains in:

```text
~/.ssh/instabounty_shadow
```

Machine-specific Ansible configuration remains in:

```text
host_vars/shadow.yml
```

Both should stay outside source control.


01  PowerShell 7
02  Windows Terminal
03  7-Zip
04  Git
05  GitHub CLI
06  VS Code
07  VS Code extensions
08  Node.js LTS
09  Corepack + pnpm
10  Python 3.13
11  uv
12  Visual Studio Build Tools
13  Docker Desktop
14  Foundry
15  Solidity tooling
16  JDK
17  Android Studio
18  Android SDK / adb / emulator
19  NVIDIA driver verification
20  Ollama
21  LLM models
22  Embedding models
23  Open WebUI