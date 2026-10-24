$ErrorActionPreference = 'Stop'

function Ensure-ServiceRunning {
    param([Parameter(Mandatory = $true)][string]$Name)

    $service = Get-Service -Name $Name -ErrorAction Stop
    if ($service.Status -ne 'Running') {
        Start-Service -Name $Name
        $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
}

Ensure-ServiceRunning -Name 'Tailscale'

$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (Test-Path -LiteralPath $tailscaleExe) {
    $hasIp = $false

    for ($attempt = 1; $attempt -le 6; $attempt++) {
        $tailscaleIp = (& $tailscaleExe ip -4 2>$null | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace($tailscaleIp)) {
            $hasIp = $true
            break
        }

        Start-Sleep -Seconds 5
    }

    if (-not $hasIp) {
        Restart-Service -Name 'Tailscale' -Force
        (Get-Service -Name 'Tailscale').WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    }
}

Ensure-ServiceRunning -Name 'sshd'

$sshListener = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if (-not $sshListener) {
    Restart-Service -Name 'sshd' -Force
    (Get-Service -Name 'sshd').WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
}
