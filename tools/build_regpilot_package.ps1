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

Set-Location $RegPilotRoot
python -m pip install -e ".[build]"
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

