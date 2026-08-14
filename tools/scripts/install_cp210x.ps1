Write-Host "Installing CP210x driver..."
$driverPath = Join-Path $PSScriptRoot "..\..\vendor\drivers\cp210x"
cd $driverPath
pnputil /add-driver silabser.inf /install
Write-Host "Exit code: $LASTEXITCODE"
Write-Host ""
Write-Host "Checking driver status..."
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'CP210|Silicon' } | Format-Table FriendlyName, Status, InstanceId -AutoSize
