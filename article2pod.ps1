# article-to-podcast.ps1 — Article to Podcast pipeline
# Place on Desktop. Subroutines live in C:\ComfyUI\scripts\

$appDir     = Split-Path -Parent $PSCommandPath
$scriptsDir = "$appDir\scripts"
$configPath = "$appDir\config.json"

# --- Load config ---
if (-not (Test-Path $configPath)) {
    Write-Host "config.json not found at: $configPath" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit
}
$config = Get-Content $configPath | ConvertFrom-Json

# --- Resolve paths from config ---
$comfyBase       = $config.comfy_base
$comfyPython     = $config.comfy_venv_python
$comfyApiUrl     = $config.comfy_url
$comfyPort       = ([System.Uri]$comfyApiUrl).Port
$comfyTimeout    = $config.comfy_startup_timeout
$tempFolder      = "$appDir\temp"
$audioFolder     = $config.audio_folder
$inputFolder     = $config.input_folder
$outputFolder    = $config.output_folder

# --- Resolve Electron paths dynamically ---
$electronBase     = "$env:LOCALAPPDATA\$($config.comfy_electron_relative)"
$comfyMain        = "$electronBase\main.py"
$comfyFrontEnd    = "$electronBase\web_custom_versions\desktop_app"
$comfyExtraModels = "$env:APPDATA\ComfyUI\extra_models_config.yaml"

# --- Ensure Python is available ---
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install Python 3.10+ and try again." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit
}

# --- Dependency check ---
$deps     = @("requests", "readability", "bs4", "PIL", "mutagen", "pyperclip", "ddgs")
$pipNames = @{ "PIL" = "Pillow"; "bs4" = "beautifulsoup4"; "ddgs" = "ddgs" }
$missing  = @()
foreach ($d in $deps) {
    $check = & python -c "import importlib.util; print(importlib.util.find_spec('$d') is not None)" 2>$null
    if ($check -ne "True") {
        $pip = if ($pipNames.ContainsKey($d)) { $pipNames[$d] } else { $d }
        $missing += $pip
    }
}
if ($missing.Count -gt 0) {
    Write-Host "`nMissing Python packages:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    Write-Host ""
    $install = Read-Host "Install them now? [Y/N]"
    if ($install.ToUpper() -eq "Y") {
        foreach ($pkg in $missing) {
            Write-Host "`nInstalling $pkg..." -ForegroundColor Cyan
            & python -m pip install $pkg
        }
        Write-Host "`nAll packages installed. Restarting..." -ForegroundColor Green
        Start-Sleep -Seconds 2
        & powershell -ExecutionPolicy Bypass -File $PSCommandPath
        exit
    } else {
        Write-Host "`nCannot continue without required packages." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit
    }
}

# --- Function: Check if ComfyUI API is responding ---
function Test-ComfyUI {
    try {
        $r = Invoke-WebRequest -Uri "$comfyApiUrl/system_stats" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

# --- Function: Start ComfyUI in background ---
function Start-ComfyUI {
    if (Test-ComfyUI) {
        Write-Host "  ComfyUI already running." -ForegroundColor Green
        return
    }
    if (-not (Test-Path $comfyPython)) {
        Write-Host "ComfyUI Python not found at: $comfyPython" -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit
    }
    if (-not (Test-Path $comfyMain)) {
        Write-Host "ComfyUI main.py not found at: $comfyMain" -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit
    }

    $arguments = @(
        $comfyMain,
        "--user-directory",           "$comfyBase\user",
        "--input-directory",          $inputFolder,
        "--output-directory",         $outputFolder,
        "--front-end-root",           $comfyFrontEnd,
        "--base-directory",           $comfyBase,
        "--database-url",             "sqlite:///$($comfyBase.Replace('\','/'))/user/comfyui.db",
        "--extra-model-paths-config", $comfyExtraModels,
        "--log-stdout",
        "--listen",                   "127.0.0.1",
        "--port",                     $comfyPort,
        "--enable-manager",
        "--preview-method",           "auto"
    )

    $script:comfyProcess = Start-Process -FilePath $comfyPython `
        -ArgumentList $arguments `
        -WorkingDirectory $comfyBase `
        -WindowStyle Hidden `
        -PassThru

    Write-Host "  ComfyUI starting in background..." -ForegroundColor DarkGray
}

# --- Function: Wait for ComfyUI to be ready ---
function Wait-ComfyUI {
    if (Test-ComfyUI) { return }
    Write-Host ""
    Write-Host "  Waiting for ComfyUI to be ready..." -ForegroundColor Cyan
    $elapsed = 0
    while (-not (Test-ComfyUI) -and $elapsed -lt $comfyTimeout) {
        Start-Sleep -Seconds 2
        $elapsed += 2
        Write-Host "  ...($elapsed s)" -ForegroundColor DarkGray
    }
    if (-not (Test-ComfyUI)) {
        Write-Host "  ComfyUI failed to start within $comfyTimeout seconds." -ForegroundColor Red
        Write-Host "  Check logs at: $env:APPDATA\ComfyUI\logs\" -ForegroundColor Yellow
        Read-Host "Press Enter to close"
        exit
    }
    Write-Host "  ComfyUI ready." -ForegroundColor Green
}

# --- Function: Shut down ComfyUI cleanly ---
function Stop-ComfyUI {
    try {
        Invoke-WebRequest -Uri "$comfyApiUrl/manager/reboot" -Method Post -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
        Start-Sleep -Seconds 2
    } catch {}

    if ($script:comfyProcess -and -not $script:comfyProcess.HasExited) {
        $script:comfyProcess | Stop-Process -Force -ErrorAction SilentlyContinue
    }

    Get-WmiObject Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -like "*ComfyUI*main.py*"
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Write-Host "  ComfyUI stopped." -ForegroundColor Gray
}

# --- Queue helpers ---
$queueFile = "$appDir\queue.json"
$slugs     = [System.Collections.ArrayList]@()
$resuming  = $false

function Load-Queue {
    if (-not (Test-Path $queueFile)) { return @() }
    $raw = Get-Content $queueFile | ConvertFrom-Json
    if (-not $raw) { return @() }
    $items = @()
    foreach ($item in $raw) {
        if ($item -is [string]) {
            $items += [PSCustomObject]@{
                slug       = $item
                status     = 'pending'
                title      = $item
                artist     = ''
                album      = ''
                album_art  = $null
                source_url = ''
                error      = $null
            }
        } else {
            if ($item.status -eq 'processing') { $item.status = 'pending' }
            $items += $item
        }
    }
    return $items
}

function Save-Queue($items) {
    $items | ConvertTo-Json -Depth 5 | Set-Content $queueFile
}

function Clean-OrphanedTemp {
    if (-not (Test-Path $tempFolder)) { return }
    $validSlugs = $allItems | ForEach-Object { $_.slug }
    Get-ChildItem $tempFolder | ForEach-Object {
        $base = $_.BaseName
        foreach ($prefix in @('audio-handoff-', 'youtube-handoff-')) {
            $base = $base -replace "^$prefix", ''
        }
        if ($base -notin $validSlugs) {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
            if ($logLevel -eq 'verbose') {
                Write-Log "Removed orphaned temp file: $($_.Name)"
            }
        }
    }
}

# ============================================================
# STARTUP
# ============================================================

Clear-Host
Write-Host "===============================" -ForegroundColor Cyan
Write-Host "     Article to Podcast" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan
Write-Host ""

# --- Ensure required folders exist ---
@("$appDir\workflow", "$appDir\log") | ForEach-Object {
    if (-not (Test-Path $_)) { New-Item -ItemType Directory -Path $_ | Out-Null }
}

# --- Load and process queue ---
$allItems     = Load-Queue
$allItems     = @($allItems | Where-Object { $_.status -ne 'done' })
$pendingItems = @($allItems | Where-Object { $_.status -in @('pending', 'failed') })

if ($pendingItems.Count -gt 0) {
    Write-Host "  Found $($pendingItems.Count) article(s) in saved queue:" -ForegroundColor Yellow
    $pendingItems | ForEach-Object { Write-Host "    - $($_.slug)" -ForegroundColor DarkGray }
    Write-Host ""
    $resume = Read-Host "  Resume this queue? [Y/N]"
    if ($resume.ToUpper() -eq "Y") {
        $pendingItems | ForEach-Object { [void]$slugs.Add($_.slug) }
        $resuming = $true
        Write-Host "  Resumed $($slugs.Count) article(s)." -ForegroundColor Green
        if (-not (Test-Path $tempFolder)) {
            New-Item -ItemType Directory -Path $tempFolder | Out-Null
        }
        Clean-OrphanedTemp
    } else {
        Remove-Item $queueFile -Force -ErrorAction SilentlyContinue
        if (Test-Path $tempFolder) { Remove-Item -Recurse -Force $tempFolder }
        New-Item -ItemType Directory -Path $tempFolder | Out-Null
        Write-Host "  Queue discarded." -ForegroundColor DarkGray
    }
} else {
    if (Test-Path $tempFolder) { Remove-Item -Recurse -Force $tempFolder }
    New-Item -ItemType Directory -Path $tempFolder | Out-Null
    Write-Host "--- Cleared temp folder ---" -ForegroundColor Cyan
}

# --- Clear any stale MP3s from failed runs ---
$staleAudio = Get-ChildItem "$audioFolder\*.mp3" -ErrorAction SilentlyContinue
if ($staleAudio) {
    $staleAudio | Remove-Item -Force
    Write-Host "  Cleared $($staleAudio.Count) stale audio file(s)."
}

# ============================================================
# URL INPUT LOOP
# ============================================================
Write-Host ""
Write-Host "===============================" -ForegroundColor Cyan
Write-Host "       Add Articles" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Enter a URL to add an article." -ForegroundColor DarkGray
Write-Host "  C  to use clipboard/reader mode." -ForegroundColor DarkGray
Write-Host "  Enter with no input to generate podcasts." -ForegroundColor DarkGray
Write-Host "  X  to cancel and exit." -ForegroundColor DarkGray

while ($true) {
    Write-Host ""
    if ($slugs.Count -gt 0) {
        Write-Host "  Queue: $($slugs.Count) article(s) ready." -ForegroundColor DarkGray
    }
    $url = Read-Host "URL"

    # --- Exit ---
    if ($url.ToUpper() -eq "X") {
        Write-Host ""
        if ($slugs.Count -gt 0) {
            $save = Read-Host "  Save current queue for next run? [Y/N]"
            if ($save.ToUpper() -eq "Y") {
                # Save in full object format
                $queueItems = $slugs | ForEach-Object {
                    $s        = $_
                    $itemMeta = Get-Content "$tempFolder\$s.json" -ErrorAction SilentlyContinue | ConvertFrom-Json
                    [PSCustomObject]@{
                        slug       = $s
                        status     = 'pending'
                        title      = if ($itemMeta) { $itemMeta.title } else { $s }
                        artist     = if ($itemMeta) { $itemMeta.artist } else { '' }
                        album      = if ($itemMeta) { $itemMeta.album } else { '' }
                        album_art  = if ($itemMeta) { $itemMeta.album_art } else { $null }
                        source_url = if ($itemMeta) { $itemMeta.source_url } else { '' }
                        error      = $null
                    }
                }
                Save-Queue $queueItems
                Write-Host "  Queue saved to $queueFile" -ForegroundColor Green
            }
        }
        Write-Host "Exiting..." -ForegroundColor Yellow
        Stop-ComfyUI
        Stop-Process -Id $PID -Force
    }

    # --- Generate ---
    if ($url -eq "") {
        if ($slugs.Count -eq 0) {
            Write-Host "  No articles queued yet." -ForegroundColor Yellow
            continue
        }
        break
    }

    # --- Clipboard mode ---
    if ($url.ToUpper() -eq "C") {
        $url = ""
    }

    # --- Step 1: Fetch article ---
    Write-Host ""
    Write-Host "--- Fetching article text ---" -ForegroundColor Cyan
    if ($url) {
        & python "$scriptsDir\fetch-article.py" $url
    } else {
        & python "$scriptsDir\fetch-article.py" --clipboard
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  fetch-article failed, skipping." -ForegroundColor Red
        Read-Host "  Press Enter to continue"
        continue
    }

    # --- Step 2: Fetch metadata ---
    Write-Host ""
    Write-Host "--- Fetching metadata + album art ---" -ForegroundColor Cyan
    if ($url) {
        & python "$scriptsDir\fetch-metadata.py" $url
    } else {
        & python "$scriptsDir\fetch-metadata.py" --clipboard
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  fetch-metadata failed, skipping." -ForegroundColor Red
        Read-Host "  Press Enter to continue"
        continue
    }

    # --- Collect the slug and save queue in object format ---
    $newJson = Get-ChildItem "$tempFolder\*.json" | Where-Object {
        $j = Get-Content $_.FullName | ConvertFrom-Json
        $j.slug -notin $slugs -and $_.Name -notlike "audio-handoff-*" -and $_.Name -notlike "youtube-handoff-*"
    } | Select-Object -First 1

    if ($newJson) {
        $meta = Get-Content $newJson.FullName | ConvertFrom-Json
        [void]$slugs.Add($meta.slug)

        # Build full object queue and save
        $queueItems = $slugs | ForEach-Object {
            $s        = $_
            $itemMeta = Get-Content "$tempFolder\$s.json" -ErrorAction SilentlyContinue | ConvertFrom-Json
            [PSCustomObject]@{
                slug       = $s
                status     = 'pending'
                title      = if ($itemMeta) { $itemMeta.title } else { $s }
                artist     = if ($itemMeta) { $itemMeta.artist } else { '' }
                album      = if ($itemMeta) { $itemMeta.album } else { '' }
                album_art  = if ($itemMeta) { $itemMeta.album_art } else { $null }
                source_url = if ($itemMeta) { $itemMeta.source_url } else { '' }
                error      = $null
            }
        }
        Save-Queue $queueItems

        Write-Host ""
        Write-Host "  Added: $($meta.slug)" -ForegroundColor Green
        Write-Host "  Queue: $($slugs.Count) article(s)" -ForegroundColor DarkGray
    } else {
        Write-Host "  Could not determine slug, skipping." -ForegroundColor Red
        Read-Host "  Press Enter to continue"
        continue
    }
}

if ($slugs.Count -eq 0) {
    Write-Host ""
    Write-Host "No articles queued. Exiting." -ForegroundColor Yellow
    Stop-ComfyUI
    Stop-Process -Id $PID -Force
}

# ============================================================
# BATCH GENERATION
# ============================================================

Write-Host ""
Write-Host "===============================" -ForegroundColor Cyan
Write-Host "     Generating $($slugs.Count) Podcast(s)" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan

# --- Log setup ---
$logLevel = $config.log_level
if (-not $logLevel) { $logLevel = "off" }
$logFile  = "$appDir\log\generation.log"

function Write-Log {
    param([string]$Message, [string]$Level = "info")
    if ($logLevel -eq "off") { return }
    if ($logLevel -eq "on_error" -and $Level -ne "error") { return }
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value "[$timestamp] $Message"
}

function Run-PythonStep {
    param([string]$StepName, [string[]]$Arguments)
    Write-Host "  $StepName..." -ForegroundColor Cyan
    Write-Log "  STEP: $StepName"
    $output = & python @Arguments 2>&1
    $output | ForEach-Object {
        Write-Host "  $_"
        Write-Log "    $_"
    }
    if ($LASTEXITCODE -ne 0) {
        $errMsg = "FAILED: $StepName (exit code $LASTEXITCODE)"
        Write-Host "  $errMsg" -ForegroundColor Red
        if ($logLevel -ne "off") {
            $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
            Add-Content -Path $logFile -Value "[$timestamp]   $errMsg"
            if ($logLevel -eq "on_error") {
                $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
                Add-Content -Path $logFile -Value "[$timestamp]   --- Output for failed step ---"
                $output | ForEach-Object {
                    Add-Content -Path $logFile -Value "[$timestamp]     $_"
                }
            }
        }
        return $false
    }
    Write-Log "  OK: $StepName"
    return $true
}

function Run-PythonStepStreamed {
    param([string]$StepName, [string[]]$Arguments)
    Write-Host "  $StepName..." -ForegroundColor Cyan
    Write-Log "  STEP: $StepName"

    $psi                        = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = "python"
    $psi.Arguments              = $Arguments -join ' '
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()

    $outputLines = [System.Collections.ArrayList]@()

    while (-not $proc.StandardOutput.EndOfStream) {
        $line = $proc.StandardOutput.ReadLine()
        if ($line -ne $null) {
            # Spinner lines use \r — write them with no newline to update in place
            if ($line.TrimStart().StartsWith('-') -or
                $line.TrimStart().StartsWith('\') -or
                $line.TrimStart().StartsWith('|') -or
                $line.TrimStart().StartsWith('/')) {
                Write-Host "`r  $($line.Trim())" -NoNewline
            } else {
                # If we were on a spinner line, move to next line first
                Write-Host ""
                Write-Host "  $line"
            }
            [void]$outputLines.Add($line)
            Write-Log "    $line"
        }
    }

    # Capture any stderr
    $errOutput = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    if ($errOutput) {
        $errOutput.Split("`n") | Where-Object { $_ } | ForEach-Object {
            Write-Host "  $_"
            Write-Log "    $_"
        }
    }

    Write-Host ""  # ensure we end on a new line

    if ($proc.ExitCode -ne 0) {
        $errMsg = "FAILED: $StepName (exit code $($proc.ExitCode))"
        Write-Host "  $errMsg" -ForegroundColor Red
        if ($logLevel -ne "off") {
            $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
            Add-Content -Path $logFile -Value "[$timestamp]   $errMsg"
            if ($logLevel -eq "on_error") {
                $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
                Add-Content -Path $logFile -Value "[$timestamp]   --- Output for failed step ---"
                $outputLines | ForEach-Object {
                    Add-Content -Path $logFile -Value "[$timestamp]     $_"
                }
            }
        }
        return $false
    }
    Write-Log "  OK: $StepName"
    return $true
}

# --- Log session start ---
if ($logLevel -ne "off") {
    $sessionStart = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value ""
    Add-Content -Path $logFile -Value "================================================"
    Add-Content -Path $logFile -Value "SESSION START: $sessionStart  [log_level: $logLevel]"
    Add-Content -Path $logFile -Value "Queued: $($slugs.Count) article(s)"
    Add-Content -Path $logFile -Value "================================================"
}

# --- Start ComfyUI now that Generate has been chosen ---
Write-Host ""
Write-Host "--- Initializing ComfyUI ---" -ForegroundColor Cyan
if (Test-ComfyUI) {
    Write-Host "  ERROR: ComfyUI is already running." -ForegroundColor Red
    Write-Host "  Please close the existing ComfyUI instance and try again." -ForegroundColor Red
    Read-Host "  Press Enter to exit"
    Stop-Process -Id $PID -Force
}
Start-ComfyUI
Wait-ComfyUI

$successCount = 0
$failCount    = 0
$failedSlugs  = [System.Collections.ArrayList]@()

foreach ($slug in $slugs) {
    Write-Host ""
    Write-Host "--- [$($slugs.IndexOf($slug) + 1)/$($slugs.Count)] $slug ---" -ForegroundColor Cyan
    Write-Log "--- ARTICLE: $slug ---"

    $audioHandoff   = "$tempFolder\audio-handoff-$slug.json"
    $youtubeHandoff = "$tempFolder\youtube-handoff-$slug.json"
    $hasDirectAudio = Test-Path $audioHandoff
    $hasYoutube     = Test-Path $youtubeHandoff
    $stepFailed     = $false

    if ($hasYoutube) {
        $handoffData = Get-Content $youtubeHandoff | ConvertFrom-Json
        $result = Run-PythonStep "Downloading YouTube audio" @("$scriptsDir\fetch-youtube.py", $handoffData.source_url, $slug)
        if ($result) {
            Remove-Item $youtubeHandoff -Force
        } else {
            $stepFailed = $true
        }
    } elseif ($hasDirectAudio) {
        $handoffData = Get-Content $audioHandoff | ConvertFrom-Json
        $result = Run-PythonStep "Downloading audio" @("$scriptsDir\fetch-audio.py", $handoffData.source_url, $slug)
        if ($result) {
            Remove-Item $audioHandoff -Force
        } else {
            $stepFailed = $true
        }
    } else {
        $slugTxt    = "$tempFolder\$slug.txt"
        $articleTxt = "$inputFolder\article.txt"
        if (-not (Test-Path $slugTxt)) {
            $msg = "txt file not found for slug: $slug"
            Write-Host "  $msg" -ForegroundColor Red
            if ($logLevel -ne "off") {
                $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
                Add-Content -Path $logFile -Value "[$timestamp]   FAILED: $msg"
            }
            $stepFailed = $true
    } else {
            Copy-Item $slugTxt $articleTxt -Force
            $result = Run-PythonStepStreamed "Generating audio via ComfyUI" @("$scriptsDir\generate-audio.py", $slug)
            if (-not $result) { $stepFailed = $true }
        }
    }

    if (-not $stepFailed) {
        $result = Run-PythonStep "Tagging and moving MP3" @("$scriptsDir\tag-mp3.py", $slug)
        if (-not $result) { $stepFailed = $true }
    }

    if ($stepFailed) {
        $failCount++
        [void]$failedSlugs.Add($slug)
        Write-Host "  FAILED: $slug" -ForegroundColor Red
        if ($logLevel -ne "off") {
            $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
            Add-Content -Path $logFile -Value "[$timestamp]   RESULT: FAILED"
        }
    } else {
        $successCount++
        Write-Host "  Done: $slug" -ForegroundColor Green
        Write-Log "  RESULT: SUCCESS"
    }

    # --- Update queue file after each item ---
    $processedIndex = $slugs.IndexOf($slug)
    $remaining      = [System.Collections.ArrayList]@()

    # Add unprocessed slugs ahead in the list
    $slugs | Select-Object -Skip ($processedIndex + 1) | ForEach-Object {
        $s        = $_
        $itemMeta = Get-Content "$tempFolder\$s.json" -ErrorAction SilentlyContinue | ConvertFrom-Json
        [void]$remaining.Add([PSCustomObject]@{
            slug       = $s
            status     = 'pending'
            title      = if ($itemMeta) { $itemMeta.title } else { $s }
            artist     = if ($itemMeta) { $itemMeta.artist } else { '' }
            album      = if ($itemMeta) { $itemMeta.album } else { '' }
            album_art  = if ($itemMeta) { $itemMeta.album_art } else { $null }
            source_url = if ($itemMeta) { $itemMeta.source_url } else { '' }
            error      = $null
        })
    }

    # Add failed slugs
    $failedSlugs | ForEach-Object {
        $s        = $_
        $itemMeta = Get-Content "$tempFolder\$s.json" -ErrorAction SilentlyContinue | ConvertFrom-Json
        [void]$remaining.Add([PSCustomObject]@{
            slug       = $s
            status     = 'failed'
            title      = if ($itemMeta) { $itemMeta.title } else { $s }
            artist     = if ($itemMeta) { $itemMeta.artist } else { '' }
            album      = if ($itemMeta) { $itemMeta.album } else { '' }
            album_art  = if ($itemMeta) { $itemMeta.album_art } else { $null }
            source_url = if ($itemMeta) { $itemMeta.source_url } else { '' }
            error      = 'See generation.log for details.'
        })
    }

    if ($remaining.Count -gt 0) {
        Save-Queue $remaining
    } else {
        if (Test-Path $queueFile) { Remove-Item $queueFile -Force }
    }
}

# --- Log session end ---
if ($logLevel -ne "off") {
    $sessionEnd = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logFile -Value ""
    Add-Content -Path $logFile -Value "================================================"
    Add-Content -Path $logFile -Value "SESSION END: $sessionEnd"
    Add-Content -Path $logFile -Value "Succeeded: $successCount  Failed: $failCount"
    if ($failedSlugs.Count -gt 0) {
        Add-Content -Path $logFile -Value "Failed articles:"
        $failedSlugs | ForEach-Object { Add-Content -Path $logFile -Value "  - $_" }
    }
    Add-Content -Path $logFile -Value "================================================"
}

# ============================================================
# DONE
# ============================================================

Write-Host ""
Write-Host "===============================" -ForegroundColor Green
Write-Host "       Podcast Ready!" -ForegroundColor Green
Write-Host "  $successCount succeeded, $failCount failed" -ForegroundColor Green
Write-Host "===============================" -ForegroundColor Green

# --- Shutdown ---
Write-Host ""
Write-Host "--- Shutting down ---" -ForegroundColor Cyan
Stop-ComfyUI
Write-Host "  Goodbye." -ForegroundColor Gray
Start-Sleep -Seconds 1
Stop-Process -Id $PID -Force