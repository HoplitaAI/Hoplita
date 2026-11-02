
function Install-Hoplita {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $repoZip = 'https://github.com/HoplitaAI/Hoplita/archive/refs/heads/main.zip'
    $uvVersion = '0.13.6'
    $pyWant = '3.12'
    $env:HOPLITA_RERUN = '& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HoplitaAI/Hoplita/main/install.ps1)))'

    $prefix = $null
    $uninstall = $false
    $child = $false
    for ($i = 0; $i -lt $args.Count; $i++) {
        $a = [string]$args[$i]
        if ($a -like '--prefix=*') { $prefix = $a.Substring(9) }
        elseif ($a -eq '--prefix' -and $i + 1 -lt $args.Count) { $prefix = [string]$args[$i + 1] }
        elseif ($a -eq '--uninstall') { $uninstall = $true }
        elseif ($a -eq '--child') { $child = $true }
    }
    if (-not $prefix -and $child -and $env:HOPLITA_SPAWN_HANDLE) {
        $base = if ($env:APPDATA) { $env:APPDATA } else { $HOME }
        $prefix = Join-Path $base ('hoplita-' + $env:HOPLITA_SPAWN_HANDLE)
    }
    if (-not $prefix) {
        $prefix = if ($env:APPDATA) { Join-Path $env:APPDATA 'hoplita' } else { Join-Path $HOME 'hoplita' }
    }

    $work = Join-Path ([IO.Path]::GetTempPath()) ('hoplita-install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Write-Host 'HOPLITA: downloading the source'
        $src = if ($env:HOPLITA_SOURCE) { $env:HOPLITA_SOURCE } else { $repoZip }
        $zip = Join-Path $work 'src.zip'
        if ($src -match '^https?://') { Invoke-WebRequest -UseBasicParsing -Uri $src -OutFile $zip }
        else { Copy-Item $src $zip }
        Expand-Archive -Path $zip -DestinationPath (Join-Path $work 'src')
        $installer = Get-ChildItem -Path (Join-Path $work 'src') -Filter install.py -Recurse -Depth 1 |
            Select-Object -First 1
        if (-not $installer) { throw 'the download holds no install.py' }

        $py = Join-Path $prefix 'venv\Scripts\python.exe'
        if ($uninstall -or -not (Test-Path $py)) {
            Write-Host "HOPLITA: fetching Python $pyWant with uv"
            $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
            $uvZip = Join-Path $work 'uv.zip'
            Invoke-WebRequest -UseBasicParsing -OutFile $uvZip `
                -Uri "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-$arch-pc-windows-msvc.zip"
            Expand-Archive -Path $uvZip -DestinationPath (Join-Path $work 'uv')
            $uv = Join-Path $work 'uv\uv.exe'
            $pyDir = if ($uninstall) { Join-Path $work 'python' } else { Join-Path $prefix 'python' }
            New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
            $env:UV_PYTHON_INSTALL_DIR = $pyDir
            $env:UV_CACHE_DIR = Join-Path $work 'uv-cache'
            & $uv python install --quiet --no-bin --no-registry $pyWant
            if ($LASTEXITCODE -ne 0) { throw "uv could not install Python $pyWant" }
            $py = (& $uv python find --managed-python $pyWant | Select-Object -First 1)
            Remove-Item Env:UV_PYTHON_INSTALL_DIR, Env:UV_CACHE_DIR
            if (-not $py -or -not (Test-Path $py)) { throw "uv could not provide Python $pyWant" }
        }
        Write-Host "HOPLITA: using $py"
        $env:PYTHONIOENCODING = 'utf-8'
        try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
        & $py $installer.FullName @args
        if ($LASTEXITCODE -ne 0) { throw "install.py ended with code $LASTEXITCODE" }
    }
    finally {
        Remove-Item Env:HOPLITA_SPAWN_TOKEN -ErrorAction SilentlyContinue
        Set-Location $HOME
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

Install-Hoplita @args
