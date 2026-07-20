# Register Instagram auto-post tasks (hidden, power-safe, disabled until login verified)
if (-not (Test-Path "C:\instagram-auto-uploader")) {
  cmd /c mklink /J "C:\instagram-auto-uploader" "$PSScriptRoot" | Out-Null
}
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\instagram-auto-uploader\run_hidden.vbs"'
$principal = New-ScheduledTaskPrincipal -UserId "atsus" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "InstagramAutoPost"  -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(10).AddMinutes(30))) -Principal $principal -Description "instagram 10:30 hidden" -Force | Out-Null
Register-ScheduledTask -TaskName "InstagramAutoPost2" -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(20).AddMinutes(30))) -Principal $principal -Description "instagram 20:30 hidden" -Force | Out-Null
foreach ($tn in "InstagramAutoPost","InstagramAutoPost2") {
  $t = Get-ScheduledTask -TaskName $tn
  $t.Settings.DisallowStartIfOnBatteries = $false
  $t.Settings.StopIfGoingOnBatteries     = $false
  $t.Settings.StartWhenAvailable         = $true
  Set-ScheduledTask -TaskName $tn -Settings $t.Settings | Out-Null
  Disable-ScheduledTask -TaskName $tn | Out-Null
}
Get-ScheduledTask -TaskName "InstagramAutoPost*" | ForEach-Object {
  $s = $_.Settings
  "{0,-18} {1,-9} {2}  battery_ok={3} catchUp={4}" -f $_.TaskName, $_.State, $_.Triggers[0].StartBoundary.Substring(11,5), (-not $s.DisallowStartIfOnBatteries), $s.StartWhenAvailable
}
