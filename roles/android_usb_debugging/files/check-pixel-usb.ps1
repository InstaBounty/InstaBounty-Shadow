# InstaBounty Pixel USB debugging diagnostics for the Shadow Windows VM.
$ErrorActionPreference = 'Stop'
$adb = 'C:\Android\Sdk\platform-tools\adb.exe'
$inf = 'C:\Android\Sdk\extras\google\usb_driver\android_winusb.inf'
Write-Host '=== InstaBounty Pixel USB Debugging ==='
if (-not (Test-Path -LiteralPath $adb)) {
  throw "ADB is missing: $adb. Run the Ansible android role."
}
Write-Host "Google USB driver downloaded: $(Test-Path -LiteralPath $inf)"
$installed = @(Get-WindowsDriver -Online | Where-Object {
  $_.OriginalFileName -and ([IO.Path]::GetFileName($_.OriginalFileName) -ieq 'android_winusb.inf')
})
Write-Host "Google USB driver in Windows Driver Store: $($installed.Count -gt 0)"
Write-Host ''
Write-Host 'ADB version:'
& $adb version
Write-Host ''
Write-Host 'ADB devices:'
# adb can hang while starting its background server or probing forwarded USB.
# Avoid inheriting this PowerShell session's handles and impose a hard limit.
$outFile = Join-Path $env:TEMP ([guid]::NewGuid().ToString() + '.adb.out')
$errFile = Join-Path $env:TEMP ([guid]::NewGuid().ToString() + '.adb.err')
$adbProcess = $null
try {
  $adbProcess = Start-Process -FilePath $adb -ArgumentList @('devices', '-l') `
    -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
  if (-not $adbProcess.WaitForExit(20000)) {
    try { $adbProcess.Kill() } catch { }
    $adbProcess.WaitForExit(5000) | Out-Null
    Write-Warning 'adb devices -l timed out after 20 seconds. Check USB forwarding and whether the ADB server is responding.'
  } else {
    Get-Content -LiteralPath $outFile -ErrorAction SilentlyContinue
    if ($adbProcess.ExitCode -ne 0) {
      Write-Warning "adb devices -l exited with code $($adbProcess.ExitCode)"
    }
  }
  $errorOutput = Get-Content -LiteralPath $errFile -ErrorAction SilentlyContinue
  if ($errorOutput) { Write-Host ($errorOutput -join [Environment]::NewLine) }
} finally {
  Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
  if ($null -ne $adbProcess) { $adbProcess.Dispose() }
}
Write-Host ''
Write-Host 'USB devices with Google VID 18D1 currently visible to Shadow:'
$google = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
  $_.InstanceId -match 'VID_18D1'
})
if ($google.Count -eq 0) {
  Write-Host '(none - check physical-PC USB forwarding in Shadow Quick Menu)'
} else {
  $google | Format-Table Status,Class,FriendlyName,InstanceId -AutoSize
}
Write-Host ''
Write-Host 'If ADB says unauthorized, unlock the Pixel and accept the RSA debugging prompt.'
Write-Host 'If ADB is empty, forward the phone from the Shadow client on the physical computer.'
