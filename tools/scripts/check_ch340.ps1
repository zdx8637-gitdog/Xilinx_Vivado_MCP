Write-Host "=== CH340 in pnputil ==="
pnputil /enum-drivers | Select-String "ch340|ch341|wch" -CaseSensitive:$false

Write-Host "=== CH340 drivers (driverquery) ==="
$result = driverquery 2>&1 | Out-String
if ($result -match "ch340|ch341|wch") {
    $result -split "`n" | Select-String "ch340|ch341|wch" -CaseSensitive:$false
} else {
    Write-Host "CH340 driver not found in driverquery"
}

Write-Host "=== CH340 USB devices ==="
Get-PnpDevice | Where-Object { $_.InstanceId -match "VID_1A86" -or $_.FriendlyName -match "CH340|CH341|WCH" } | Format-Table FriendlyName, Status, InstanceId -AutoSize

Write-Host "=== Check if ch34*.sys exists ==="
Get-ChildItem "C:\Windows\System32\drivers" -Filter "ch34*" 2>$null | Select-Object Name, Length
Get-ChildItem "C:\Windows\System32\DriverStore\FileRepository" -Recurse -Filter "ch34*" -Depth 2 -ErrorAction SilentlyContinue | Select-Object Name, Directory
