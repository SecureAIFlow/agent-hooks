# SecureAIFlow agent-hooks - Windows PowerShell one-line installer.
#
#   .\install.ps1 cursor                          # from a clone
#   iex "& { $(irm https://<host>/install.ps1) } cursor"   # straight from the web
#
# Thin shim over install.py: finds a working Python 3 (cloning the bundle first
# when run from the web), then hands off. Same result as install.bat / install.sh.

param([string]$Ide = "cursor")

$ErrorActionPreference = "Stop"

function Die($msg) { Write-Error $msg; exit 1 }

# Resolve the bundle: local clone next to this script, or clone from the web.
$repoUrl = if ($env:SAF_REPO_URL) { $env:SAF_REPO_URL } else { "https://github.com/SecureAIFlow/agent-hooks.git" }
$here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }

if ($here -and (Test-Path (Join-Path $here "install.py"))) {
    $root = $here
} else {
    $root = Join-Path $HOME ".secureaiflow\agent-hooks"
    if (Test-Path (Join-Path $root "install.py")) {
        git -C $root pull --quiet 2>$null
    } else {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git is required to install from the web" }
        git clone --quiet $repoUrl $root
        if ($LASTEXITCODE -ne 0) { Die "clone failed" }
    }
}

# Find a Python that actually runs (the Microsoft Store stub exits non-zero).
$py = $null
foreach ($c in @("py", "python", "python3")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        & $c -c "import json,http.client" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    }
}
if (-not $py) { Die "Python 3 is required on PATH. Install it from python.org and retry." }

& $py (Join-Path $root "install.py") $Ide
exit $LASTEXITCODE
