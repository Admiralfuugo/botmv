# Loyihani ishga tushirish:  .\ishga.ps1
# PostgreSQL to'xtagan bo'lsa yoqadi, eski bot jarayonini to'xtatadi va botni ishga tushiradi.
Set-Location $PSScriptRoot
$pg = "C:\Users\fuugo\pgsql\bin\pg_ctl.exe"
$data = "C:\Users\fuugo\pgdata"

& $pg -D $data status 2>$null | Out-Null
# log data papkasidan tashqarida: aks holda Windows'da tiklanish paytida "sharing violation" bilan 30+ s qotadi
if ($LASTEXITCODE -ne 0) { & $pg -D $data -l "C:\Users\fuugo\pgsql\server.log" -w start }

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*bot.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

& .\.venv\Scripts\python.exe bot.py
