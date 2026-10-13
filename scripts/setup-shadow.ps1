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

function Read-SshPublicKey {
    while ($true) {
        Write-Host ''
        Write-Host 'Paste the public key used by the Ansible control machine.'
        Write-Host 'Example: ssh-ed25519 AAAAC3... instabounty-shadow'
        $key = Read-Host 'SSH public key'
        $key = $key.Trim()

        if ($key -match '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+[A-Za-z0-9+/]+={0,3}(\s+.*)?$') {
            return $key
        }

        Write-Warning 'That does not look like a valid OpenSSH public key. Paste the complete contents of the .pub file.'
    }
}

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-AuthorizedKey {
    param(
        [string]$AuthorizedKeysPath,
        [string]$PublicKey
    )

    $directory = Split-Path -Parent $AuthorizedKeysPath
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $existingKeys = @()
    if (Test-Path $AuthorizedKeysPath) {
        $existingKeys = @(Get-Content -Path $AuthorizedKeysPath -ErrorAction Stop)
    }

    if ($existingKeys -contains $PublicKey) {
        Write-Host 'SSH public key is already present.'
        return
    }

    Add-Content -Path $AuthorizedKeysPath -Value $PublicKey -Encoding ascii
    Write-Host "SSH public key added to $AuthorizedKeysPath"
}

Write-Host 'InstaBounty Shadow Bootstrap' -ForegroundColor Green
Write-Host 'This prepares a clean Shadow Windows VM so that Ansible can provision it.'
Write-Host 'Run this script from Windows PowerShell as Administrator.'

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$defaultUser = ($currentIdentity -split '\\')[-1]

Write-Step 'Collecting configuration'
$windowsUser = Read-RequiredValue -Prompt 'Windows username Ansible should use' -DefaultValue $defaultUser
$sshPublicKey = Read-SshPublicKey
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

Write-Step 'Installing the Ansible SSH public key'
$localUser = Get-LocalUser -Name $windowsUser -ErrorAction SilentlyContinue
if (-not $localUser) {
    throw "Windows user '$windowsUser' does not exist on this machine."
}

$administratorsGroup = Get-LocalGroup -SID 'S-1-5-32-544'
$isAdministrator = $false
try {
    $isAdministrator = @(Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop | Where-Object {
        $_.SID -eq $localUser.SID
    }).Count -gt 0
}
catch {
    Write-Warning "Could not determine whether '$windowsUser' belongs to the local Administrators group. Falling back to the per-user authorized_keys file."
}

if ($isAdministrator) {
    $authorizedKeysPath = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
    Add-AuthorizedKey -AuthorizedKeysPath $authorizedKeysPath -PublicKey $sshPublicKey

    & icacls $authorizedKeysPath /inheritance:r | Out-Null
    & icacls $authorizedKeysPath /grant '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to set permissions on administrators_authorized_keys.'
    }

    Write-Host 'Configured the OpenSSH administrator authorized-keys file.'
}
else {
    $profile = Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $localUser.SID.Value } | Select-Object -First 1
    if ($profile -and $profile.LocalPath) {
        $userProfilePath = $profile.LocalPath
    }
    else {
        $userProfilePath = Join-Path $env:SystemDrive "Users\$windowsUser"
    }

    $sshDirectory = Join-Path $userProfilePath '.ssh'
    $authorizedKeysPath = Join-Path $sshDirectory 'authorized_keys'
    Add-AuthorizedKey -AuthorizedKeysPath $authorizedKeysPath -PublicKey $sshPublicKey

    & icacls $sshDirectory /inheritance:r | Out-Null
    & icacls $sshDirectory /grant:r "${windowsUser}:(OI)(CI)F" '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to set permissions on the user .ssh directory.'
    }

    & icacls $authorizedKeysPath /inheritance:r | Out-Null
    & icacls $authorizedKeysPath /grant:r "${windowsUser}:F" '*S-1-5-18:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to set permissions on authorized_keys.'
    }

    Write-Host "Configured the authorized-keys file for '$windowsUser'."
}

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
Write-Host "Authorized key: $authorizedKeysPath"
Write-Host 'SSH service   : Running'
Write-Host 'SSH port      : 22'

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
Write-Host '2. Create host_vars/shadow.yml on the control machine.'
Write-Host '3. Test direct SSH connectivity using the matching private key.'
Write-Host '4. Test Ansible connectivity before running the full playbook.'
