Write-Host "=== All USB devices (by connection) ==="
Get-PnpDevice | Where-Object { $_.InstanceId -match '^USB' } | Select-Object FriendlyName, Status, InstanceId | Format-Table -AutoSize

Write-Host "=== All devices with problems ==="
Get-PnpDevice | Where-Object { $_.Problem -ne 0 -or $_.Status -ne 'OK' } | Select-Object FriendlyName, Status, Problem, InstanceId | Format-Table -AutoSize

Write-Host "=== FTDI installed drivers (pnputil) ==="
pnputil /enum-drivers | Select-String -Pattern "FTDI|ftdi|ftd"

Write-Host "=== Check for recently added devices ==="
Get-PnpDevice | Where-Object { $_.InstanceId -match 'FTDI|ftd|USB\\\\VID_0403' } | Format-Table FriendlyName, Status, InstanceId -AutoSize
