Write-Host "=== Searching for FTDI drivers in Vivado ==="
Get-ChildItem -Path "D:\Xilinx\Vivado\2023.1\data\xicom\cable_drivers" -Recurse -Include "*.inf" | ForEach-Object { Write-Host $_.FullName }
Write-Host ""
Write-Host "=== Checking all USB devices ==="
Get-PnpDevice | Where-Object { $_.InstanceId -match '^USB' } | Format-Table FriendlyName, Status, InstanceId -AutoSize
Write-Host ""
Write-Host "=== Checking drivers via driverquery ==="
driverquery | findstr -i "ftdi digilent cp210 silicon xilinx"
