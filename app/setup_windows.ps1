<#
Offline Tutor: one-time Windows setup (no admin needed). Re-running is safe: finished steps are skipped.

  powershell -ExecutionPolicy Bypass -File app\setup_windows.ps1 -ModelPath <gguf> -PackPath <pack.jsonl>

Installs into %LOCALAPPDATA%\OfflineTutor:
  venv\            private Python 3.12 (via uv) with the app's packages
  bin\llama\       llama.cpp llama-server (CUDA 12.4 build, pinned tag)
  models\          gemma-base-Q4_K_M.gguf, pack.jsonl, sherpa-onnx-whisper-small (speech-to-text),
                   kokoro-en-v0_19 (voice)
  src\             tutor\ + tutor_app\ (copied from this repo; re-run to update)
  OfflineTutor.cmd and Desktop / Start-menu shortcuts
Internet is needed only for this setup. The app itself runs fully offline.
#>
param([string]$ModelPath = "", [string]$PackPath = "")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is much faster without the progress bar

$Repo = Split-Path -Parent $PSScriptRoot
$HomeDir = Join-Path $env:LOCALAPPDATA "OfflineTutor"
$Dl = Join-Path $HomeDir "downloads"
$LlamaTag = "b11714"            # llama.cpp release, 2026-10-17 (Gemma-4 support)
$KatexVer = "0.16.11"
$MarkedVer = "12.0.2"
foreach ($d in "bin", "models", "src", "logs", "downloads") { New-Item -ItemType Directory -Force (Join-Path $HomeDir $d) | Out-Null }

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fetch($url, $out) {
    if (-not (Test-Path $out)) {
        Write-Host "   downloading $url"
        Invoke-WebRequest -Uri $url -OutFile "$out.part" -UseBasicParsing
        Move-Item "$out.part" $out -Force
    }
}

Step "uv (Python manager)"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    winget install --id astral-sh.uv -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $uv = (Get-Command uv).Source
}
Write-Host "   $uv"

Step "Python 3.12 environment + packages"
$Py = Join-Path $HomeDir "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { & $uv venv --python 3.12 (Join-Path $HomeDir "venv") }
& $uv pip install --python $Py -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE) { throw "package install failed" }
& $uv pip freeze --python $Py | Set-Content -Encoding utf8 (Join-Path $PSScriptRoot "requirements.lock.txt")

Step "llama.cpp $LlamaTag (CUDA 12.4)"
$LlamaDir = Join-Path $HomeDir "bin\llama"
if (-not (Test-Path (Join-Path $LlamaDir "llama-server.exe"))) {
    $base = "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaTag"
    Fetch "$base/llama-$LlamaTag-bin-win-cuda-12.4-x64.zip" (Join-Path $Dl "llama-$LlamaTag.zip")
    Fetch "$base/cudart-llama-bin-win-cuda-12.4-x64.zip" (Join-Path $Dl "cudart-12.4.zip")
    Expand-Archive (Join-Path $Dl "llama-$LlamaTag.zip") -DestinationPath $LlamaDir -Force
    Expand-Archive (Join-Path $Dl "cudart-12.4.zip") -DestinationPath $LlamaDir -Force
    $exe = Get-ChildItem $LlamaDir -Recurse -Filter llama-server.exe | Select-Object -First 1
    if ($exe.DirectoryName -ne $LlamaDir) { Get-ChildItem $exe.DirectoryName | Move-Item -Destination $LlamaDir -Force }
}
cmd /c "`"$(Join-Path $LlamaDir 'llama-server.exe')`" --version 2>&1" | Select-Object -Last 2 | Out-Host  # via cmd: PS 5.1 turns native stderr into errors

Step "Speech-to-text model (Whisper-small, multilingual)"
$Wh = Join-Path $HomeDir "models\sherpa-onnx-whisper-small"
if (-not (Test-Path (Join-Path $Wh "small-encoder.int8.onnx"))) {
    Fetch "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-small.tar.bz2" (Join-Path $Dl "whisper-small.tar.bz2")
    tar -xjf (Join-Path $Dl "whisper-small.tar.bz2") -C (Join-Path $HomeDir "models")
    Get-ChildItem $Wh -Filter "*.onnx" | Where-Object { $_.Name -notmatch "int8" } | Remove-Item   # keep only int8
    if (Test-Path (Join-Path $Wh "test_wavs")) { Remove-Item -Recurse (Join-Path $Wh "test_wavs") }
}

Step "Voice (Kokoro English v0.19 TTS)"
$Ko = Join-Path $HomeDir "models\kokoro-en-v0_19"
if (-not (Test-Path (Join-Path $Ko "voices.bin"))) {
    $local = Join-Path $Repo "models\speech\kokoro-en-v0_19"   # already present from the Phase 0 speech benchmark
    if (Test-Path (Join-Path $local "voices.bin")) {
        robocopy $local $Ko /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "copying Kokoro failed" }
        $global:LASTEXITCODE = 0
    } else {
        Fetch "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-en-v0_19.tar.bz2" (Join-Path $Dl "kokoro-en-v0_19.tar.bz2")
        tar -xjf (Join-Path $Dl "kokoro-en-v0_19.tar.bz2") -C (Join-Path $HomeDir "models")
    }
}

Step "Hindi voices for Hinglish answers (Kokoro v1.0 multi-lang, Piper hi_IN) + pronunciation lexicon"
$TtsBase = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"
foreach ($name in "kokoro-multi-lang-v1_0", "vits-piper-hi_IN-priyamvada-medium", "vits-piper-hi_IN-rohan-medium",
                  "vits-piper-hi_IN-pratham-medium") {
    if (-not (Test-Path (Join-Path $HomeDir "models\$name"))) {
        Fetch "$TtsBase/$name.tar.bz2" (Join-Path $Dl "$name.tar.bz2")
        tar -xjf (Join-Path $Dl "$name.tar.bz2") -C (Join-Path $HomeDir "models")
    }
}
$Lex = Join-Path $Repo "models\hinglish_lexicon.json"   # built on the server by train/build_lexicon.py
if (Test-Path $Lex) { Copy-Item $Lex (Join-Path $HomeDir "models\hinglish_lexicon.json") -Force }
else { Write-Warning "models\hinglish_lexicon.json missing: Hinglish speech will use spelling rules only" }

Step "Offline web assets for the chat window (KaTeX $KatexVer, marked $MarkedVer)"
$Assets = Join-Path $Repo "app\tutor_app\assets"
if (-not (Test-Path (Join-Path $Assets "katex\katex.min.js"))) {
    Fetch "https://registry.npmjs.org/katex/-/katex-$KatexVer.tgz" (Join-Path $Dl "katex.tgz")
    $k = Join-Path $Dl "katex"
    New-Item -ItemType Directory -Force $k | Out-Null
    tar -xzf (Join-Path $Dl "katex.tgz") -C $k
    New-Item -ItemType Directory -Force (Join-Path $Assets "katex") | Out-Null
    Copy-Item (Join-Path $k "package\dist\katex.min.css"), (Join-Path $k "package\dist\katex.min.js") (Join-Path $Assets "katex")
    Copy-Item -Recurse -Force (Join-Path $k "package\dist\fonts") (Join-Path $Assets "katex")
}
Fetch "https://cdn.jsdelivr.net/npm/marked@$MarkedVer/marked.min.js" (Join-Path $Assets "marked.min.js")

Step "App source -> $HomeDir\src"
foreach ($pair in @(@("tutor", "tutor"), @("app\tutor_app", "tutor_app"))) {
    robocopy (Join-Path $Repo $pair[0]) (Join-Path $HomeDir "src\$($pair[1])") /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying $($pair[0]) failed" }
}
$global:LASTEXITCODE = 0

Step "Model and knowledge pack"
$Model = Join-Path $HomeDir "models\gemma-base-Q4_K_M.gguf"
if ($ModelPath) {
    Copy-Item $ModelPath $Model -Force
    $side = "$ModelPath.sha256"
    if (Test-Path $side) {
        $want = (Get-Content $side).Split(" ")[0].Trim()
        $got = (Get-FileHash $Model -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $want) { throw "model hash mismatch: $got vs $want" }
        Write-Host "   model SHA-256 verified"
    }
}
if ($PackPath) { Copy-Item $PackPath (Join-Path $HomeDir "models\pack.jsonl") -Force }
if (-not (Test-Path $Model)) { Write-Warning "No model yet: re-run with -ModelPath <gemma-base-Q4_K_M.gguf>" }

Step "Launcher and shortcuts"
$Pyw = Join-Path $HomeDir "venv\Scripts\pythonw.exe"
@"
@echo off
cd /d "%~dp0src"
start "" "%~dp0venv\Scripts\pythonw.exe" -m tutor_app.main
"@ | Set-Content -Encoding ascii (Join-Path $HomeDir "OfflineTutor.cmd")
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Offline Tutor.lnk"))
    $lnk.TargetPath = $Pyw
    $lnk.Arguments = "-m tutor_app.main"
    $lnk.WorkingDirectory = Join-Path $HomeDir "src"
    $lnk.Description = "Offline Reliability Engineering tutor"
    $lnk.Save()
}
Write-Host "`nDone. Start 'Offline Tutor' from the Desktop or Start menu (or run $HomeDir\OfflineTutor.cmd)." -ForegroundColor Green
