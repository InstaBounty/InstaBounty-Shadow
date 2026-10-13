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
├── host_vars/
│   ├── shadow.yml
│   └── shadow.example.yml
└── roles/
```

`host_vars/shadow.yml` contains machine-specific or sensitive configuration and must not be committed.

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

Postman is installed as the Windows desktop application and is intended for manual API testing during Gateway and service development.

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
