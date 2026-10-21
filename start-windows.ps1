$ErrorActionPreference = 'Stop'
$AppDirectory = $PSScriptRoot
Set-Location -LiteralPath $AppDirectory

function Test-PythonVersion([string] $PythonPath) {
    if (!(Test-Path -LiteralPath $PythonPath -PathType Leaf)) { return $false }
    & $PythonPath -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' *> $null
    return ($LASTEXITCODE -eq 0)
}

function Find-SystemPython {
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        foreach ($Version in @('-3.14', '-3.13', '-3.12', '-3.11', '-3.10', '-3')) {
            $Found = & $PyLauncher.Source $Version -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $Found -and (Test-PythonVersion $Found.Trim())) {
                return $Found.Trim()
            }
        }
    }
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $Found = & $PythonCommand.Source -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $Found -and (Test-PythonVersion $Found.Trim())) {
            return $Found.Trim()
        }
    }
    return $null
}

try {
    $RuntimeDirectory = Join-Path $AppDirectory '.runtime'
    $Uv = Get-Command uv.exe -ErrorAction SilentlyContinue
    $UvPath = if ($Uv) { $Uv.Source } else { Join-Path $RuntimeDirectory 'bin\uv.exe' }
    $HasUv = $Uv -or (Test-Path -LiteralPath $UvPath -PathType Leaf)

    $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeDirectory 'python'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $VenvPython = Join-Path $AppDirectory '.venv\Scripts\python.exe'
    $FirstRun = !(Test-PythonVersion $VenvPython)
    if ($FirstRun) {
        Remove-Item -LiteralPath (Join-Path $AppDirectory '.venv') -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ($FirstRun) {
        $SystemPython = Find-SystemPython
        if ($SystemPython) {
            Write-Host "Creating the local Python environment with $SystemPython..."
            & $SystemPython -m venv (Join-Path $AppDirectory '.venv')
            if ($LASTEXITCODE -ne 0) {
                Write-Host 'The system Python cannot create a virtual environment; using managed Python instead.'
                Remove-Item -LiteralPath (Join-Path $AppDirectory '.venv') -Recurse -Force -ErrorAction SilentlyContinue
                $SystemPython = $null
            }
        }

        if (!$SystemPython) {
            if (!$HasUv) {
                Write-Host 'Downloading the small uv setup tool...'
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
                    (Join-Path $AppDirectory 'bootstrap-uv.ps1') -InstallDirectory $AppDirectory | Out-Null
                if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $UvPath -PathType Leaf)) {
                    throw 'Could not download uv. Check your internet connection and try again.'
                }
            }
            Write-Host 'No usable Python 3.10+ was found. Downloading a private Python 3.12 runtime...'
            & $UvPath python install 3.12
            if ($LASTEXITCODE -ne 0) { throw 'Could not download Python 3.12.' }
            & $UvPath venv --python 3.12 (Join-Path $AppDirectory '.venv')
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
        }
    }

    if (!(Test-PythonVersion $VenvPython)) { throw 'The local Python environment could not be started.' }

    Write-Host 'Installing ClearMark dependencies...'
    if ($HasUv -or (Test-Path -LiteralPath $UvPath -PathType Leaf)) {
        & $UvPath pip install --python $VenvPython -r (Join-Path $AppDirectory 'requirements.txt')
    } else {
        & $VenvPython -m pip install -r (Join-Path $AppDirectory 'requirements.txt')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not install ClearMark dependencies.' }

    & $VenvPython -c 'import torch; from pathlib import Path; raise SystemExit(0 if (Path(torch.hub.get_dir()) / "checkpoints" / "big-lama.pt").is_file() else 1)' *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Downloading the LaMa AI model (~200 MB, one time)...'
        & $VenvPython -c 'import remover; remover._get_lama()'
        if ($LASTEXITCODE -ne 0) { throw 'Could not download the AI model. Run this launcher again to retry.' }
    }

    $OptionalSkip = Join-Path $RuntimeDirectory 'exiftool-skipped'
    & $VenvPython -c 'from detector import exiftool_path; raise SystemExit(0 if exiftool_path() else 1)' *> $null
    if ($LASTEXITCODE -ne 0 -and !(Test-Path -LiteralPath $OptionalSkip)) {
        $Answer = Read-Host 'Install optional ExifTool for richer metadata checks? [y/N]'
        if ($Answer -match '^(y|yes)$') {
            & $VenvPython (Join-Path $AppDirectory 'install_optional.py') exiftool
            if ($LASTEXITCODE -ne 0) { Write-Host 'ExifTool was skipped; metadata checks will use reduced mode.' }
        } else {
            New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
            New-Item -ItemType File -Path $OptionalSkip -Force | Out-Null
            Write-Host 'Skipping optional ExifTool.'
        }
    }

    Write-Host 'Adding ClearMark to Windows Start search...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $AppDirectory 'register-windows-app.ps1') -InstallDirectory $AppDirectory
    if ($LASTEXITCODE -ne 0) { Write-Host 'Start Menu registration failed; ClearMark can still start here.' }

    Write-Host 'Starting the ClearMark desktop window...'
    & $VenvPython (Join-Path $AppDirectory 'desktop.py')
    if ($LASTEXITCODE -ne 0) { throw 'ClearMark closed with an error.' }
} catch {
    Write-Host "`nClearMark setup/start failed: $($_.Exception.Message)" -ForegroundColor Red
    if ([Environment]::UserInteractive) { Read-Host 'Press Enter to close' | Out-Null }
    exit 1
}
