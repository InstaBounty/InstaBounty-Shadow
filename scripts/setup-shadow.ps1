#Requires -RunAsAdministrator
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Read-RequiredValue {
    param(
        [string]$Prompt,
        [string]$DefaultValue = ''
    )

    while ($true) {
        if ($DefaultValue) {
            $value = Read-Host "$Prompt [$DefaultValue]"
            if ([string]::IsNullOrWhiteSpace($value)) { return $DefaultValue }
        }
        else {
            $value = Read-Host $Prompt
        }

        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
        Write-Warning 'A value is required.'
    }
}

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host 'InstaBounty Shadow Bootstrap' -ForegroundColor Green
Write-Host 'This prepares a clean Shadow Windows VM so that Ansible can provision it.'
Write-Host 'Run this script from Windows PowerShell as Administrator.'

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$defaultUser = ($currentIdentity -split '\\')[-1]

Write-Step 'Collecting configuration'
$windowsUser = Read-RequiredValue -Prompt 'Windows username Ansible should use' -DefaultValue $defaultUser
$installTailscale = Read-Host 'Install Tailscale automatically? [Y/n]'
if ([string]::IsNullOrWhiteSpace($installTailscale)) { $installTailscale = 'Y' }

Write-Step 'Checking OpenSSH Server'
$sshCapability = Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Server*' | Select-Object -First 1
if (-not $sshCapability) {
    throw 'Windows OpenSSH Server capability could not be found.'
}

if ($sshCapability.State -ne 'Installed') {
    Write-Host 'Installing OpenSSH Server...'
    Add-WindowsCapability -Online -Name $sshCapability.Name | Out-Null
}
else {
    Write-Host 'OpenSSH Server is already installed.'
}

Write-Step 'Configuring the SSH service'
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

Write-Step 'Configuring the Windows Firewall'
$firewallRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if (-not $firewallRule) {
    New-NetFirewallRule `
        -Name 'OpenSSH-Server-In-TCP' `
        -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -Action Allow `
        -LocalPort 22 | Out-Null
}
else {
    Enable-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'
}

Write-Step 'Setting PowerShell as the default SSH shell'
$openSshRegistryPath = 'HKLM:\SOFTWARE\OpenSSH'
if (-not (Test-Path $openSshRegistryPath)) {
    New-Item -Path $openSshRegistryPath -Force | Out-Null
}
New-ItemProperty `
    -Path $openSshRegistryPath `
    -Name DefaultShell `
    -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -PropertyType String `
    -Force | Out-Null

Restart-Service sshd

if ($installTailscale -match '^[Yy]') {
    Write-Step 'Installing Tailscale'

    if (Test-CommandExists 'tailscale') {
        Write-Host 'Tailscale is already installed.'
    }
    elseif (Test-CommandExists 'winget') {
        & winget install --id Tailscale.Tailscale --exact --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw "winget failed to install Tailscale (exit code $LASTEXITCODE)."
        }
    }
    else {
        throw 'winget is unavailable, so Tailscale cannot be installed automatically. Install Tailscale manually and rerun this script.'
    }

    $tailscaleService = Get-Service -Name 'Tailscale' -ErrorAction SilentlyContinue
    if ($tailscaleService) {
        Set-Service -Name 'Tailscale' -StartupType Automatic
        if ($tailscaleService.Status -ne 'Running') { Start-Service -Name 'Tailscale' }
    }

    $tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path $tailscaleExe) {
        Write-Host 'Starting Tailscale authentication. A browser window may open.'
        & $tailscaleExe up
    }
    else {
        Write-Warning 'Tailscale was installed but tailscale.exe was not found at the expected path. Open Tailscale from the Start menu and sign in.'
    }
}

Write-Step 'Verifying SSH'
$sshd = Get-Service sshd
if ($sshd.Status -ne 'Running') {
    throw 'The sshd service is not running.'
}

$sshListener = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if (-not $sshListener) {
    throw 'Nothing is listening on TCP port 22.'
}

Write-Step 'Reading network information'
$tailscaleIp = $null
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (Test-Path $tailscaleExe) {
    try {
        $tailscaleIp = (& $tailscaleExe ip -4 2>$null | Select-Object -First 1).Trim()
    }
    catch {
        $tailscaleIp = $null
    }
}

Write-Host "`nBootstrap completed successfully." -ForegroundColor Green
Write-Host "Windows user : $windowsUser"
if ($tailscaleIp) {
    Write-Host "Tailscale IP : $tailscaleIp"
}
else {
    Write-Host 'Tailscale IP : not available yet'
}
Write-Host 'SSH service  : Running'
Write-Host 'SSH port     : 22'

Write-Host "`nUse these values on the Ansible control machine in host_vars/shadow.yml:"
Write-Host '---'
if ($tailscaleIp) {
    Write-Host "ansible_host: $tailscaleIp"
}
else {
    Write-Host 'ansible_host: YOUR_TAILSCALE_IP'
}
Write-Host "ansible_user: $windowsUser"
Write-Host ''
Write-Host 'postgresql_superuser_password: "YOUR_STRONG_PASSWORD_HERE"'

Write-Host "`nNext steps:" -ForegroundColor Yellow
Write-Host '1. Ensure the local/control PC is signed into the same Tailscale network.'
Write-Host '2. Install the Ansible SSH public key for this Windows user.'
Write-Host '3. Create host_vars/shadow.yml on the control machine.'
Write-Host '4. Test SSH and Ansible connectivity before running the full playbook.'
