# Open a dedicated, local browser profile per InstaBounty test identity.
# No cookies, cached logins or browser extensions are shared across profiles.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Chrome', 'Firefox')]
  [string]$Browser,

  [Parameter(Mandatory = $true)]
  [ValidateSet('publisher', 'support', 'player')]
  [string]$Role,

  [string]$Url = 'about:blank'
)

$ErrorActionPreference = 'Stop'
$base = Join-Path $env:LOCALAPPDATA 'InstaBounty\TestBrowsers'
$profile = Join-Path (Join-Path $base $Browser) $Role
New-Item -ItemType Directory -Force -Path $profile | Out-Null

if ($Browser -eq 'Chrome') {
  $paths = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
  )
} else {
  $paths = @(
    (Join-Path $env:ProgramFiles 'Mozilla Firefox\firefox.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Mozilla Firefox\firefox.exe')
  )
}
$exe = $paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $exe) { throw "$Browser is not installed. Run the Ansible browsers role." }

if ($Browser -eq 'Chrome') {
  Start-Process -FilePath $exe -ArgumentList @("--user-data-dir=`"$profile`"", '--no-first-run', "`"$Url`"")
} else {
  # Firefox -no-remote allows a different profile to run alongside other sessions.
  Start-Process -FilePath $exe -ArgumentList @('-no-remote', '-profile', "`"$profile`"", "`"$Url`"")
}
Write-Output "Opened $Browser with isolated '$Role' test profile."
