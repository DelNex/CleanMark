param(
    [Parameter(Mandatory = $true)]
    [string] $InstallDirectory,
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'
$InstallDirectory = [System.IO.Path]::GetFullPath($InstallDirectory)
$Programs = [Environment]::GetFolderPath('Programs')
$ShortcutPath = Join-Path $Programs 'ClearMark.lnk'

if ($Uninstall) {
    Remove-Item -LiteralPath $ShortcutPath -Force -ErrorAction SilentlyContinue
    Write-Output 'ClearMark was removed from Windows app search.'
    exit 0
}

$Python = Join-Path $InstallDirectory '.venv\Scripts\pythonw.exe'
$EntryPoint = Join-Path $InstallDirectory 'desktop.py'
$Icon = Join-Path $InstallDirectory 'static\clearmark.ico'

if (!(Test-Path -LiteralPath $Python -PathType Leaf) -or
    !(Test-Path -LiteralPath $EntryPoint -PathType Leaf)) {
    throw 'ClearMark is not fully set up yet; cannot create the Start Menu shortcut.'
}

New-Item -ItemType Directory -Path $Programs -Force | Out-Null
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = '"' + $EntryPoint + '"'
$Shortcut.WorkingDirectory = $InstallDirectory
$Shortcut.Description = 'Remove watermarks from images and videos on this computer'
if (Test-Path -LiteralPath $Icon -PathType Leaf) {
    $Shortcut.IconLocation = $Icon
}
$Shortcut.Save()

Write-Output 'ClearMark is available in Windows Start search.'
