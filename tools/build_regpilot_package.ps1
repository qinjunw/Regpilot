param(
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"
$RegPilotRoot = Split-Path -Parent $PSScriptRoot
$AppIcon = Join-Path $RegPilotRoot "resources\regpilot.ico"
$PromptDir = Join-Path $RegPilotRoot "src\regulation_agent\prompts"
$StaticDir = Join-Path $RegPilotRoot "static"
$SkillsDir = Join-Path $RegPilotRoot "skills"
$TargetDir = Join-Path $RegPilotRoot $OutputDir
$SourceDir = Join-Path $RegPilotRoot "src"
$OriginalPythonPath = $env:PYTHONPATH
$OriginalPythonNoUserSite = $env:PYTHONNOUSERSITE

python -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Activate a clean Python virtual environment before building RegPilot."
}

try {
    $env:PYTHONPATH = $SourceDir
    $env:PYTHONNOUSERSITE = "1"
    Set-Location $RegPilotRoot

    python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install RegPilot build dependencies."
    }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --name "Regpilot法规合规领航员" `
        --icon $AppIcon `
        --paths "src" `
        --add-data "$PromptDir;regulation_agent\prompts" `
        --add-data "$StaticDir;static" `
        --add-data "$SkillsDir;skills" `
        --distpath $TargetDir `
        "src\regulation_agent\desktop_entry.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the RegPilot package."
    }
}
finally {
    if ($null -eq $OriginalPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $OriginalPythonPath
    }

    if ($null -eq $OriginalPythonNoUserSite) {
        Remove-Item Env:PYTHONNOUSERSITE -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONNOUSERSITE = $OriginalPythonNoUserSite
    }
}

