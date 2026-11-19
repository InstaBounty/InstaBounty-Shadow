# Open a dedicated, local browser profile per InstaBounty test identity.
# No cookies, cached logins or browser extensions are shared across profiles.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Chrome', 'Firefox', 'Edge', 'WebKit')]
  [string]$Browser,

  [Parameter(Mandatory = $true)]
  [ValidateSet('publisher', 'support', 'player', 'user')]
  [string]$Role,

  [string]$Url = 'about:blank'
)

$ErrorActionPreference = 'Stop'
$base = Join-Path $env:LOCALAPPDATA 'InstaBounty\TestBrowsers'
$profile = Join-Path (Join-Path $base $Browser) $Role
New-Item -ItemType Directory -Force -Path $profile | Out-Null

if ($Browser -eq 'WebKit') {
  $node = 'C:\Program Files\nodejs\node.exe'
  $runner = 'C:\InstaBounty\tools\open-test-webkit.cjs'
  if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $runner)) {
    throw 'Playwright WebKit is not provisioned. Run the Ansible browsers role.'
  }
  Start-Process -FilePath $node -ArgumentList @(
    "`"$runner`"", "`"$profile`"", "`"$Url`""
  )
  Write-Output "Opened WebKit with isolated '$Role' test profile."
  exit 0
}

if ($Browser -eq 'Chrome') {
  $paths = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
  )
} elseif ($Browser -eq 'Edge') {
  $paths = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
  )
} else {
  $paths = @(
    (Join-Path $env:ProgramFiles 'Mozilla Firefox\firefox.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Mozilla Firefox\firefox.exe')
  )
}
$exe = $paths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $exe) { throw "$Browser is not installed. Run the Ansible browsers role." }

if ($Browser -in @('Chrome', 'Edge')) {
  Start-Process -FilePath $exe -ArgumentList @("--user-data-dir=`"$profile`"", '--no-first-run', "`"$Url`"")
} else {
  # Firefox -no-remote allows a different profile to run alongside other sessions.
  Start-Process -FilePath $exe -ArgumentList @('-no-remote', '-profile', "`"$profile`"", "`"$Url`"")
}
Write-Output "Opened $Browser with isolated '$Role' test profile."
