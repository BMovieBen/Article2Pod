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
$comfyPort  = ([System.Uri]$comfyApiUrl).Port
$comfyTimeout    = $config.comfy_startup_timeout
$tempFolder = "$appDir\temp"
$audioFolder     = $config.audio_folder
$inputFolder     = $config.input_folder
$outputFolder    = $config.output_folder

# --- Resolve Electron paths dynamically ---
$electronBase    = "$env:LOCALAPPDATA\$($config.comfy_electron_relative)"
$comfyMain       = "$electronBase\main.py"
$comfyFrontEnd   = "$electronBase\web_custom_versions\desktop_app"
$comfyExtraModels= "$env:APPDATA\ComfyUI\extra_models_config.yaml"

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

# --- Function: Start ComfyUI in background (don't wait) ---
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

# ============================================================
# STARTUP
# ============================================================

Clear-Host
Write-Host "===============================" -ForegroundColor Cyan
Write-Host "     Article to Podcast" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan
Write-Host ""

# --- Check for saved queue before clearing temp ---
$queueFile = "$appDir\queue.json"
$slugs     = [System.Collections.ArrayList]@()
$resuming  = $false

if (Test-Path $queueFile) {
    $savedQueue = Get-Content $queueFile | ConvertFrom-Json
    if ($savedQueue -and $savedQueue.Count -gt 0) {
        Write-Host ""
        Write-Host "  Found saved queue from previous run:" -ForegroundColor Yellow
        $savedQueue | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkGray }
        Write-Host ""
        $resume = Read-Host "  Resume this queue? [Y/N]"
        if ($resume.ToUpper() -eq "Y") {
            $slugs    = [System.Collections.ArrayList]@($savedQueue)
            $resuming = $true
            Write-Host "  Resumed $($slugs.Count) article(s) from saved queue." -ForegroundColor Green
        } else {
            Remove-Item $queueFile -Force
            Write-Host "  Saved queue discarded." -ForegroundColor DarkGray
        }
    }
}

# --- Clear any stale MP3s from failed runs ---
$staleAudio = Get-ChildItem "$audioFolder\*.mp3" -ErrorAction SilentlyContinue
if ($staleAudio) {
    $staleAudio | Remove-Item -Force
    Write-Host "  Cleared $($staleAudio.Count) stale audio file(s)."
}

# --- Ensure required folders exist and clear temp only if not resuming ---
@("$appDir\workflow", "$appDir\log") | ForEach-Object {
    if (-not (Test-Path $_)) { New-Item -ItemType Directory -Path $_ | Out-Null }
}

if ($resuming) {
    Write-Host "--- Keeping temp folder (resuming previous session) ---" -ForegroundColor Cyan
    Write-Host "  Preserved: $tempFolder"
    # Still create it if somehow missing
    if (-not (Test-Path $tempFolder)) {
        New-Item -ItemType Directory -Path $tempFolder | Out-Null
    }
} else {
    Write-Host "--- Clearing temp folder ---" -ForegroundColor Cyan
    if (Test-Path $tempFolder) { Remove-Item -Recurse -Force $tempFolder }
    New-Item -ItemType Directory -Path $tempFolder | Out-Null
    Write-Host "  Cleared: $tempFolder"
}

# --- Start ComfyUI in background immediately, don't wait ---
Write-Host ""
Write-Host "--- Starting ComfyUI in background ---" -ForegroundColor Cyan
Start-ComfyUI

# ============================================================
# URL INPUT LOOP
# ============================================================
Write-Host ""
Write-Host "===============================" -ForegroundColor Cyan
Write-Host "       Add Articles" -ForegroundColor Cyan
Write-Host "===============================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Enter a URL to add an article." -ForegroundColor DarkGray
Write-Host "  Press Enter with no URL for clipboard/reader mode." -ForegroundColor DarkGray
Write-Host "  G to stop adding and generate podcasts." -ForegroundColor DarkGray
Write-Host "  X to cancel and exit." -ForegroundColor DarkGray

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
                $slugs | ConvertTo-Json | Set-Content $queueFile
                Write-Host "  Queue saved to $queueFile" -ForegroundColor Green
            }
        }
        Write-Host "Exiting..." -ForegroundColor Yellow
        Stop-ComfyUI
        Stop-Process -Id $PID -Force
    }

    # --- Generate ---
    if ($url.ToUpper() -eq "G") {
        if ($slugs.Count -eq 0) {
            Write-Host "  No articles queued yet." -ForegroundColor Yellow
            continue
        }
        break
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

    # --- Collect the slug from temp ---
    $newJson = Get-ChildItem "$tempFolder\*.json" | Where-Object {
        $j = Get-Content $_.FullName | ConvertFrom-Json
        $j.slug -notin $slugs -and $_.Name -notlike "audio-handoff-*" -and $_.Name -notlike "youtube-handoff-*"
    } | Select-Object -First 1
    if ($newJson) {
        $meta = Get-Content $newJson.FullName | ConvertFrom-Json
        $slugs += $meta.slug
        # Save queue after every successful add
        $slugs | ConvertTo-Json | Set-Content $queueFile
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

# --- Wait for ComfyUI now if it isn't ready yet ---
Wait-ComfyUI

$successCount = 0
$failCount    = 0
$failedSlugs  = [System.Collections.ArrayList]@()

foreach ($slug in $slugs) {
    Write-Host ""
    Write-Host "--- [$($slugs.IndexOf($slug) + 1)/$($slugs.Count)] $slug ---" -ForegroundColor Cyan

    $audioHandoff   = "$tempFolder\audio-handoff-$slug.json"
    $youtubeHandoff = "$tempFolder\youtube-handoff-$slug.json"
    $hasDirectAudio = Test-Path $audioHandoff
    $hasYoutube     = Test-Path $youtubeHandoff
    $stepFailed     = $false

    if ($hasYoutube) {
        $handoffData = Get-Content $youtubeHandoff | ConvertFrom-Json
        Write-Host "  Downloading YouTube audio..." -ForegroundColor Cyan
        & python "$scriptsDir\fetch-youtube.py" $handoffData.source_url $slug
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  fetch-youtube failed for: $slug" -ForegroundColor Red
            $stepFailed = $true
        } else {
            Remove-Item $youtubeHandoff -Force
        }
    } elseif ($hasDirectAudio) {
        $handoffData = Get-Content $audioHandoff | ConvertFrom-Json
        Write-Host "  Downloading audio directly..." -ForegroundColor Cyan
        & python "$scriptsDir\fetch-audio.py" $handoffData.source_url $slug
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  fetch-audio failed for: $slug" -ForegroundColor Red
            $stepFailed = $true
        } else {
            Remove-Item $audioHandoff -Force
        }
    } else {
        $slugTxt    = "$tempFolder\$slug.txt"
        $articleTxt = "$inputFolder\article.txt"
        if (-not (Test-Path $slugTxt)) {
            Write-Host "  txt file not found for slug: $slug, skipping." -ForegroundColor Red
            $stepFailed = $true
        } else {
            Copy-Item $slugTxt $articleTxt -Force
            & python "$scriptsDir\generate-audio.py" $slug
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  generate-audio failed for: $slug" -ForegroundColor Red
                $stepFailed = $true
            }
        }
    }

    if (-not $stepFailed) {
        & python "$scriptsDir\tag-mp3.py" $slug
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  tag-mp3 failed for: $slug" -ForegroundColor Red
            $stepFailed = $true
        }
    }

    if ($stepFailed) {
        $failCount++
        [void]$failedSlugs.Add($slug)
    } else {
        $successCount++
    }

    # Rewrite queue file with remaining unprocessed + failed slugs
    $processedIndex = $slugs.IndexOf($slug)
    $unprocessed    = [System.Collections.ArrayList]@($slugs | Select-Object -Skip ($processedIndex + 1))
    $failedSlugs | ForEach-Object { [void]$unprocessed.Add($_) }
    if ($unprocessed.Count -gt 0) {
        $unprocessed | ConvertTo-Json | Set-Content $queueFile
    } else {
        if (Test-Path $queueFile) { Remove-Item $queueFile -Force }
    }
}

# --- Save failed slugs back to queue file for retry ---
if ($failedSlugs.Count -gt 0) {
    $failedSlugs | ConvertTo-Json | Set-Content $queueFile
    Write-Host ""
    Write-Host "  $($failedSlugs.Count) article(s) failed and saved to queue for retry." -ForegroundColor Yellow
    Write-Host "  Run the script again to retry them." -ForegroundColor Yellow
} else {
    # All succeeded — clear the queue file
    if (Test-Path $queueFile) { Remove-Item $queueFile -Force }
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