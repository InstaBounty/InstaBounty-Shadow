# Gina provisioning

This role installs the Gina Slack coordinator on the Shadow development workstation.

It:

- clones or updates `InstaBounty/gina` into `C:\Users\Shadow\instabounty\gina`;
- runs `uv sync --group dev`;
- installs `gina-start.cmd` and `gina-check.cmd` in `C:\Program Files\InstaBounty\bin`;
- adds that bin directory to the machine PATH;
- verifies that Gina imports successfully.

Provision only Gina with:

```bash
ansible-playbook -i inventory.yml playbook.yml --tags gina -e perform_final_reboot=false
```

Open a fresh terminal after provisioning so PATH changes are visible.

Before running `gina-start`, configure these environment variables on Shadow:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

The role intentionally does not store or template Slack secrets and does not auto-start Gina yet. Once the Slack round-trip is verified, Gina can be promoted to an always-on service in a follow-up change.
