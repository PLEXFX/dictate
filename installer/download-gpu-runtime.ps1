<#
.SYNOPSIS
Fetches the CUDA compute DLLs (cuBLAS, cuDNN, cuBLAS' cuda_nvrtc sibling)
straight from PyPI and lays them out exactly the way gpu_runtime.py's own
runtime_dir()/is_installed() expect, so the installer can give a GPU-task
install a head start before Dictate ever runs.

This is a Pascal-Script-callable mirror of gpu_runtime.py's own
_latest_win_amd64_wheel()/download_and_install() -- kept as close to that
logic as PowerShell allows so the two never quietly diverge. If anything
here fails (offline install, PyPI hiccup, a wheel layout change), this
script just exits non-zero -- GPU acceleration files are then available
from Settings' own "Download now" button (Engine.start_gpu_download(),
settings_window.py), not automatically; this script is a head start for
the common case, not the only path to a working GPU install.

Deliberately sequential, one package at a time -- unlike gpu_runtime.py's
own in-process ThreadPoolExecutor version (real, lightweight threads inside
one already-running process), parallelizing this script means spinning up
separate powershell.exe processes (Start-Job), and that measured *slower*
in practice (11+ minutes vs. a consistent ~2-3 for the sequential version
in real testing) -- the per-process overhead outweighed the concurrent-
download saving for just three files. Not worth the complexity or the risk
of a long, silent stall during setup for something that's already a
best-effort head start, not the only path to a working install.

.PARAMETER DestDir
The nvidia/ folder to populate -- {app}\_internal\nvidia in the installed
layout. Replaced atomically (built in a temp dir, then moved into place)
so a script killed partway through (Ctrl+C on the installer, machine
sleep) never leaves is_installed() seeing a half-populated folder.

.PARAMETER ProgressFile
Polled by dictate.iss's own DownloadGpuRuntime() so the wizard can show a
real percentage while this runs in the background (Exec's ewNoWait, since
a blocking Exec would freeze the whole Pascal Script thread -- including
its own UI repaints -- for the download's entire length). Overwritten with
a plain "NN" integer on every throttled progress tick, and with the
terminal "SUCCESS" or "FAILED: <message>" as the very last write -- that
terminal line is also this script's own signal to the polling loop that
it's done, not just a status report.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$DestDir,
    [Parameter(Mandatory = $true)]
    [string]$ProgressFile
)

$ErrorActionPreference = 'Stop'
$UserAgent = 'dictate-installer'
$DownloadChunkBytes = 65536

# Mirrors gpu_runtime.py's _PACKAGE_SUBDIRS / _CUDNN_MAX_MAJOR exactly --
# if either changes there, change it here too.
$Packages = @(
    @{ Name = 'nvidia-cublas-cu12';     Subdir = 'cublas';     MaxMajor = -1 }
    @{ Name = 'nvidia-cudnn-cu12';      Subdir = 'cudnn';      MaxMajor = 10 }
    @{ Name = 'nvidia-cuda-nvrtc-cu12'; Subdir = 'cuda_nvrtc'; MaxMajor = -1 }
)

function Get-LatestWheel {
    param([string]$Package, [int]$MaxMajor)

    $json = Invoke-RestMethod -Uri "https://pypi.org/pypi/$Package/json" -UserAgent $UserAgent -TimeoutSec 20
    $candidates = @()
    foreach ($prop in $json.releases.PSObject.Properties) {
        $normalized = ($prop.Name -replace '[^0-9.]', '')
        if ([string]::IsNullOrEmpty($normalized)) { continue }
        try { $verObj = [version]$normalized } catch { continue }
        if ($MaxMajor -ge 0 -and $verObj.Major -ge $MaxMajor) { continue }
        foreach ($file in $prop.Value) {
            if ($file.filename -like '*-win_amd64.whl' -and -not $file.yanked) {
                $candidates += [PSCustomObject]@{ Version = $verObj; Url = $file.url; Size = [int64]$file.size }
            }
        }
    }
    if ($candidates.Count -eq 0) { return $null }
    return ($candidates | Sort-Object -Property Version -Descending | Select-Object -First 1)
}

# A hand-rolled streaming download (System.Net.WebRequest, not
# Invoke-WebRequest) is what makes real chunk-level progress possible at
# all -- Invoke-WebRequest has no per-chunk hook, only "done" or "not done."
# Mirrors updater.py's/gpu_runtime.py's own _download()'s callback shape:
# OnChunk gets (bytesReadSoFarForThisFile, totalBytesForThisFile).
function Invoke-DownloadWithProgress {
    param([string]$Url, [string]$OutFile, [scriptblock]$OnChunk)

    $request = [System.Net.WebRequest]::Create($Url)
    $request.UserAgent = $UserAgent
    $response = $request.GetResponse()
    try {
        $total = $response.ContentLength
        $responseStream = $response.GetResponseStream()
        $fileStream = [System.IO.File]::Create($OutFile)
        try {
            $buffer = New-Object byte[] $DownloadChunkBytes
            $readSoFar = 0
            while (($bytesRead = $responseStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $fileStream.Write($buffer, 0, $bytesRead)
                $readSoFar += $bytesRead
                & $OnChunk $readSoFar $total
            }
        } finally {
            $fileStream.Close()
            $responseStream.Close()
        }
    } finally {
        $response.Close()
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

$tempRoot = Join-Path $env:TEMP ('dictate-gpu-' + [guid]::NewGuid().ToString('N'))
$extractRoot = Join-Path $tempRoot 'nvidia'
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

# Same throttle shape as engine.py's own on_bytes callback -- at most one
# progress-file write per 1% or per 100ms, whichever comes first, so a fast
# connection's flood of chunk callbacks doesn't turn into a flood of disk
# writes the wizard's poll loop has to keep up with.
$lastWrittenPercent = -1
$lastWriteTime = Get-Date

function Write-Progress-ToFile {
    param([int]$DoneBytes, [int]$TotalBytes)
    if ($TotalBytes -le 0) { return }
    $percent = [Math]::Min(100, [int](($DoneBytes / $TotalBytes) * 100))
    $now = Get-Date
    if ($percent -eq $lastWrittenPercent -and ($now - $lastWriteTime).TotalMilliseconds -lt 100) { return }
    $script:lastWrittenPercent = $percent
    $script:lastWriteTime = $now
    # A genuine, reproduced-in-testing race: the installer's own polling
    # loop (LoadStringFromFile, dictate.iss) reads this same file on its
    # own timer, and Windows can hand back a sharing-violation IOException
    # ("...being used by another process") if that read lands mid-write.
    # With $ErrorActionPreference = 'Stop' active, an uncaught exception
    # here -- from what is genuinely just a cosmetic progress update --
    # would abort the entire download. -ErrorAction SilentlyContinue does
    # NOT catch this: it's a raw thrown .NET IOException, not a
    # PowerShell non-terminating error record, so only a real try/catch
    # actually stops it from propagating. Missing one throttled tick is
    # harmless; the next one (100ms/1% later) supersedes it regardless.
    try {
        Set-Content -Path $ProgressFile -Value $percent -NoNewline
    } catch {
        # Swallowed on purpose -- see comment above.
    }
}

# Unlike Write-Progress-ToFile's own throttled ticks (missing one is
# invisible -- the next tick supersedes it), the terminal SUCCESS/FAILED
# line is dictate.iss's own polling loop's *only* signal that this script
# is done at all; losing it to the same sharing-violation race would leave
# that loop spinning until its own timeout instead of noticing right away.
# A few short retries make that realistically not happen without risking a
# real hang of their own.
function Write-TerminalProgress {
    param([string]$Value)
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Set-Content -Path $ProgressFile -Value $Value -NoNewline
            return
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
}

try {
    # Resolve every wheel's URL and size up front, exactly like
    # gpu_runtime.py's own download_and_install() does, so overall percent
    # is against the true combined total from the first byte -- not a
    # guess that has to be revised once later packages' sizes are known.
    $resolved = @()
    foreach ($pkg in $Packages) {
        $wheel = Get-LatestWheel -Package $pkg.Name -MaxMajor $pkg.MaxMajor
        if (-not $wheel) { throw "No matching win_amd64 wheel found for $($pkg.Name)" }
        $resolved += [PSCustomObject]@{ Pkg = $pkg; Url = $wheel.Url; Size = $wheel.Size }
    }
    $totalBytes = ($resolved | Measure-Object -Property Size -Sum).Sum
    $completedBytes = 0

    foreach ($item in $resolved) {
        $pkg = $item.Pkg
        $baseBytes = $completedBytes
        $wheelPath = Join-Path $tempRoot ($pkg.Name + '.whl')

        Invoke-DownloadWithProgress -Url $item.Url -OutFile $wheelPath -OnChunk {
            param($readSoFar, $fileTotal)
            Write-Progress-ToFile -DoneBytes ($baseBytes + $readSoFar) -TotalBytes $totalBytes
        }
        $completedBytes = $baseBytes + $item.Size

        # Expand-Archive requires a .zip extension; a wheel already is one.
        $zipPath = [System.IO.Path]::ChangeExtension($wheelPath, '.zip')
        Rename-Item -Path $wheelPath -NewName (Split-Path $zipPath -Leaf)

        $prefix = "nvidia/$($pkg.Subdir)/bin/"
        $matched = $false
        $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            foreach ($entry in $zip.Entries) {
                if ($entry.FullName.StartsWith($prefix) -and -not $entry.FullName.EndsWith('/')) {
                    $matched = $true
                    # Strip the leading "nvidia/" (7 chars) -- extractRoot is
                    # already the nvidia/ folder itself.
                    $destPath = Join-Path $extractRoot $entry.FullName.Substring(7)
                    $destParent = Split-Path $destPath -Parent
                    if (-not (Test-Path $destParent)) {
                        New-Item -ItemType Directory -Path $destParent -Force | Out-Null
                    }
                    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destPath, $true)
                }
            }
        } finally {
            $zip.Dispose()
        }
        if (-not $matched) { throw "Wheel for $($pkg.Name) had no files under $prefix" }
        Remove-Item $zipPath -Force
    }

    if (Test-Path $DestDir) { Remove-Item $DestDir -Recurse -Force }
    $destParent = Split-Path $DestDir -Parent
    if (-not (Test-Path $destParent)) { New-Item -ItemType Directory -Path $destParent -Force | Out-Null }
    Move-Item -Path $extractRoot -Destination $DestDir -Force
    Write-TerminalProgress -Value 'SUCCESS'
} catch {
    Write-TerminalProgress -Value "FAILED: $($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
