$ErrorActionPreference = "Stop"

$toolDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $env:WINDIR "pyw.exe"
$script = Join-Path $toolDir "RUN_MASTER_TOOL.pyw"
$desktopTargets = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
    (Join-Path $env:USERPROFILE "Desktop"),
    (Join-Path $env:PUBLIC "Desktop")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

if (-not (Test-Path -LiteralPath $target)) {
    throw "Khong tim thay pyw.exe: $target"
}
if (-not (Test-Path -LiteralPath $script)) {
    throw "Khong tim thay launcher: $script"
}

$shell = New-Object -ComObject WScript.Shell
$created = @()
foreach ($desktop in $desktopTargets) {
    foreach ($name in @("MO TOOL MASTER.lnk", "Master Content Voice Tool.lnk")) {
        try {
            $shortcutPath = Join-Path $desktop $name
            $shortcut = $shell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $target
            $shortcut.Arguments = "-3 `"$script`""
            $shortcut.WorkingDirectory = $toolDir
            $shortcut.Description = "Mo Master Content Voice Tool"
            $shortcut.IconLocation = "$target,0"
            $shortcut.Save()
            $created += $shortcutPath
        }
        catch {
            Write-Warning "Khong tao duoc shortcut trong $desktop`: $($_.Exception.Message)"
        }
    }
}

if (-not $created) {
    throw "Khong tao duoc shortcut nao tren Desktop."
}

Write-Host "Created shortcuts:"
$created | ForEach-Object { Write-Host $_ }
