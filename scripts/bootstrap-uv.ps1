param(
    [Parameter(Mandatory = $true)]
    [string] $InstallDirectory
)

$ErrorActionPreference = 'Stop'
$InstallDirectory = [System.IO.Path]::GetFullPath($InstallDirectory)
$UvDirectory = Join-Path $InstallDirectory '.runtime\bin'
New-Item -ItemType Directory -Path $UvDirectory -Force | Out-Null

# Use Astral's documented standalone installer without changing the user's PATH.
$env:UV_INSTALL_DIR = $UvDirectory
$env:UV_NO_MODIFY_PATH = '1'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallDirectory '.runtime\python'
$env:UV_PYTHON_INSTALL_BIN = '0'
Invoke-RestMethod 'https://astral.sh/uv/install.ps1' | Invoke-Expression

$Uv = Join-Path $UvDirectory 'uv.exe'
if (!(Test-Path -LiteralPath $Uv -PathType Leaf)) {
    throw 'The uv setup tool did not finish installing.'
}
Write-Output $Uv
