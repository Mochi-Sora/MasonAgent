param(
    [switch]$Dev,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

# This installer installs a local nanobot checkout in editable mode, so the
# running command always reflects the source tree it was installed from.
$Package = "nanobot-ai"
# The public repository URL. It enables remote installs without an explicit
# --git flag.
$DefaultGitUrl = "https://github.com/Mochi-Sora/MasonAgent"
$script:RequestedRepo = $null
$script:RequestedGit = $env:NANOBOT_INSTALL_GIT
$script:RepoRoot = $null
$script:InstallSource = ""
$script:NanobotRunner = $null
$script:NanobotPython = $null
$script:NanobotBin = $null
$script:LastInstallSucceeded = $false

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Fail {
    param([string]$Message)
    throw "Error: $Message"
}

function Show-InstallFailureHint {
    [Console]::Error.WriteLine("Error: could not install nanobot from $($script:InstallSource).")
    [Console]::Error.WriteLine("If pip mentioned externally-managed-environment, use uv, pipx, or a virtual environment instead of system pip.")
    [Console]::Error.WriteLine("You can also run manually:")
    [Console]::Error.WriteLine("  uv tool install --force --upgrade --editable $($script:RepoRoot)")
    [Console]::Error.WriteLine("  $Python -m venv `$HOME\.nanobot\venv")
    [Console]::Error.WriteLine("  `$HOME\.nanobot\venv\Scripts\python.exe -m pip install --upgrade --editable $($script:RepoRoot)")
    [Console]::Error.WriteLine("Then start setup with:")
    [Console]::Error.WriteLine("  nanobot onboard --wizard")
    throw "could not install nanobot from $($script:InstallSource)"
}

function Show-Usage {
    Write-Host "Usage: install.ps1 [-DryRun] [--repo PATH] [--git URL]"
    Write-Host ""
    Write-Host "By default this installs the nanobot checkout that contains this script"
    Write-Host "in editable mode. Run it from a clone:"
    Write-Host ""
    Write-Host "  .\scripts\install.ps1"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  --repo PATH  Install a different local checkout in editable mode."
    Write-Host "  --git URL    Clone (or update) the repository into"
    Write-Host "               `$HOME\.nanobot\src and install from that clone."
    Write-Host "  -DryRun      Print what would happen without installing or starting setup."
    Write-Host ""
    Write-Host "Environment:"
    Write-Host "  NANOBOT_SKIP_WIZARD=1   Skip the setup wizard."
    Write-Host "  NANOBOT_VENV=path       Use a different managed virtual environment."
    Write-Host "  NANOBOT_SRC_DIR=path    Clone into a different directory for --git."
    Write-Host "  NANOBOT_INSTALL_GIT=URL Default repository for --git."
    Write-Host "  PYTHON=python           Use a specific Python interpreter."
}

function Test-Checkout {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $Pyproject = Join-Path $Path "pyproject.toml"
    $InitFile = Join-Path $Path "nanobot\__init__.py"
    if (-not (Test-Path -LiteralPath $Pyproject) -or -not (Test-Path -LiteralPath $InitFile)) {
        return $false
    }
    $Content = Get-Content -LiteralPath $Pyproject -Raw
    return [bool]($Content -match '(?m)^name\s*=\s*"nanobot-ai"')
}

function Resolve-GitCheckout {
    param([string]$Url)
    $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $null }
    if ($env:NANOBOT_SRC_DIR) {
        $SrcDir = $env:NANOBOT_SRC_DIR
    } elseif ($HomeDir) {
        $SrcDir = Join-Path $HomeDir ".nanobot\src"
    } else {
        Fail "Installing from a git URL needs HOME or NANOBOT_SRC_DIR to choose a clone directory."
    }
    if ($DryRun) {
        Write-Info "Dry run: would clone or update $Url in $SrcDir."
    } else {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Fail "Installing from a git URL requires git on PATH."
        }
        if (Test-Path -LiteralPath (Join-Path $SrcDir ".git")) {
            Write-Info "Updating $SrcDir from $Url..."
            & git -C $SrcDir pull --ff-only *> $null
            if ($LASTEXITCODE -ne 0) {
                Fail "Could not update $SrcDir. Remove that directory and rerun, or use --repo with a local checkout."
            }
        } else {
            Write-Info "Cloning $Url into $SrcDir..."
            $Parent = Split-Path -Parent $SrcDir
            if ($Parent) {
                New-Item -ItemType Directory -Force -Path $Parent *> $null
            }
            & git clone --depth 1 $Url $SrcDir
            if ($LASTEXITCODE -ne 0) {
                Fail "Could not clone $Url into $SrcDir."
            }
        }
    }
    $script:RepoRoot = $SrcDir
    if ((-not $DryRun) -or (Test-Path -LiteralPath $script:RepoRoot)) {
        if (-not (Test-Checkout $script:RepoRoot)) {
            Fail "The repository at $($script:RepoRoot) does not look like a nanobot checkout."
        }
    }
}

function Resolve-Checkout {
    if ($script:RequestedRepo) {
        if (-not (Test-Path -LiteralPath $script:RequestedRepo)) {
            Fail "--repo directory not found: $($script:RequestedRepo)"
        }
        $Candidate = (Resolve-Path -LiteralPath $script:RequestedRepo).Path
        if (-not (Test-Checkout $Candidate)) {
            Fail "--repo $Candidate is not a nanobot checkout (expected pyproject.toml with name = `"nanobot-ai`")"
        }
        $script:RepoRoot = $Candidate
        return
    }

    if ($script:RequestedGit) {
        Resolve-GitCheckout $script:RequestedGit
        return
    }

    if ($PSScriptRoot) {
        $Candidate = Split-Path -Parent $PSScriptRoot
        if (Test-Checkout $Candidate) {
            $script:RepoRoot = $Candidate
            return
        }
    }

    if ($DefaultGitUrl) {
        Resolve-GitCheckout $DefaultGitUrl
        return
    }

    Fail "No nanobot checkout found. Run this script from a clone (.\scripts\install.ps1), pass --repo PATH, or pass --git URL."
}

function Test-Python {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-Python {
    if ($env:PYTHON) {
        if (Get-Command $env:PYTHON -ErrorAction SilentlyContinue) {
            if (Test-Python $env:PYTHON) {
                return $env:PYTHON
            }
            Fail "PYTHON=$env:PYTHON is not Python 3.11 or newer."
        }
        Fail "PYTHON=$env:PYTHON was not found."
    }

    foreach ($Candidate in @("python", "py")) {
        if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
            if (Test-Python $Candidate) {
                return $Candidate
            }
        }
    }

    Fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
}

function Test-VirtualEnv {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-Pip {
    param([string]$Command)

    try {
        & $Command -m pip --version *> $null
    } catch {}

    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Info "pip was not found for $Command. Trying ensurepip..."
    & $Command -m ensurepip --upgrade *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "pip is not available. Install pip for $Command, then rerun this command."
    }
}

function Invoke-Nanobot {
    param([string[]]$NanobotArgs)

    switch ($script:NanobotRunner) {
        "uv" {
            & uv tool run --from $Package nanobot @NanobotArgs
        }
        "pipx" {
            & pipx run --spec $Package nanobot @NanobotArgs
        }
        "direct" {
            & $script:NanobotBin @NanobotArgs
        }
        "python" {
            & $script:NanobotPython -m nanobot @NanobotArgs
        }
        default {
            Fail "nanobot was installed, but no runner was configured."
        }
    }
}

function Get-NanobotCommand {
    switch ($script:NanobotRunner) {
        "uv" { return "uv tool run --from $Package nanobot" }
        "pipx" { return "pipx run --spec $Package nanobot" }
        "direct" { return $script:NanobotBin }
        "python" { return "$script:NanobotPython -m nanobot" }
        default { return "nanobot" }
    }
}

function Test-FreshNanobotInstall {
    $HomeDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not $HomeDir) {
        return $false
    }
    return -not (Test-Path -LiteralPath (Join-Path $HomeDir ".nanobot\config.json"))
}

function Test-BrowserSession {
    if ($env:SSH_CONNECTION -or $env:SSH_TTY -or -not [Environment]::UserInteractive) {
        return $false
    }

    $CurrentSessionId = (Get-Process -Id $PID).SessionId
    return @(
        Get-Process -Name explorer -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $CurrentSessionId }
    ).Count -gt 0
}

function Install-WithActivePython {
    Write-Info "Detected an active virtual environment. Installing $($script:InstallSource) into it..."
    Ensure-Pip $Python
    & $Python -m pip install --upgrade --editable $script:RepoRoot
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }
    $script:NanobotRunner = "python"
    $script:NanobotPython = $Python
}

function Install-WithUv {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing $($script:InstallSource) with uv tool in editable mode..."
    & uv tool install --python $Python --force --upgrade --editable $script:RepoRoot
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $script:NanobotRunner = "uv"
    $script:LastInstallSucceeded = $true
}

function Install-WithPipx {
    $script:LastInstallSucceeded = $false
    Write-Info "Installing $($script:InstallSource) with pipx in editable mode..."
    & pipx install --python $Python --force --editable $script:RepoRoot
    if ($LASTEXITCODE -ne 0) {
        return
    }
    $PipxBinDir = $null
    try {
        $PipxBinDir = (& pipx environment --value PIPX_BIN_DIR 2>$null | Select-Object -First 1)
    } catch {}
    if (-not $PipxBinDir) {
        $HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
        if ($HomeDir) {
            $PipxBinDir = Join-Path $HomeDir ".local\bin"
        }
    }
    if ($PipxBinDir) {
        $PipxBinDir = "$PipxBinDir".Trim()
        $Candidate = Join-Path $PipxBinDir "nanobot.exe"
        if (Test-Path -LiteralPath $Candidate) {
            $script:NanobotRunner = "direct"
            $script:NanobotBin = $Candidate
            $script:LastInstallSucceeded = $true
            return
        }
    }
    $script:NanobotRunner = "pipx"
    $script:LastInstallSucceeded = $true
}

function Install-WithManagedVenv {
    $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $null }
    if (-not $HomeDir) {
        Fail "HOME is not set; cannot create a managed virtual environment."
    }

    $VenvDir = if ($env:NANOBOT_VENV) { $env:NANOBOT_VENV } else { Join-Path $HomeDir ".nanobot\venv" }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (-not (Test-Path $VenvPython)) {
        Write-Info "Creating a dedicated virtual environment at $VenvDir..."
        $Parent = Split-Path -Parent $VenvDir
        if ($Parent) {
            New-Item -ItemType Directory -Force -Path $Parent *> $null
        }
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Show-InstallFailureHint
        }
    }

    if (-not (Test-Python $VenvPython)) {
        Fail "The managed venv uses Python older than 3.11. Remove it or set NANOBOT_VENV to a new path."
    }

    Write-Info "Installing $($script:InstallSource) in $VenvDir..."
    Ensure-Pip $VenvPython
    & $VenvPython -m pip install --upgrade --editable $script:RepoRoot
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }

    $script:NanobotRunner = "python"
    $script:NanobotPython = $VenvPython
}

for ($Index = 0; $Index -lt $RemainingArgs.Count; $Index++) {
    $Arg = $RemainingArgs[$Index]
    switch ($Arg) {
        "--dev" {
            $Dev = $true
        }
        "--dry-run" {
            $DryRun = $true
        }
        "--repo" {
            if ($Index + 1 -ge $RemainingArgs.Count) {
                Fail "--repo requires a path"
            }
            $Index++
            $script:RequestedRepo = $RemainingArgs[$Index]
        }
        "--git" {
            if ($Index + 1 -ge $RemainingArgs.Count) {
                Fail "--git requires a URL"
            }
            $Index++
            $script:RequestedGit = $RemainingArgs[$Index]
        }
        "-h" {
            Show-Usage
            return
        }
        "--help" {
            Show-Usage
            return
        }
        default {
            Fail "Unknown option: $Arg"
        }
    }
}

if ($Dev) {
    Write-Info "-Dev is the default now: this installer always installs a checkout in editable mode."
}

$Python = Find-Python
$Version = & $Python --version
Write-Info "Using Python: $Version"

Resolve-Checkout
$script:InstallSource = "the checkout at $($script:RepoRoot)"

if ($DryRun) {
    Write-Info "Dry run: would install or upgrade nanobot from $($script:InstallSource)."
    if (Test-VirtualEnv $Python) {
        Write-Info "Dry run: active virtual environment detected; would run: $Python -m pip install --upgrade --editable $script:RepoRoot"
        Write-Info "Dry run: would run nanobot as: $Python -m nanobot"
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: uv tool install --python $Python --force --upgrade --editable $script:RepoRoot"
        Write-Info "Dry run: would run nanobot as: uv tool run --from $Package nanobot"
    } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        Write-Info "Dry run: would run: pipx install --python $Python --force --editable $script:RepoRoot"
        Write-Info "Dry run: would run nanobot as: pipx run --spec $Package nanobot"
    } else {
        $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { "~" }
        $VenvDir = if ($env:NANOBOT_VENV) { $env:NANOBOT_VENV } else { Join-Path $HomeDir ".nanobot\venv" }
        Write-Info "Dry run: would create or reuse a dedicated virtual environment: $VenvDir"
        Write-Info "Dry run: would run: $VenvDir\Scripts\python.exe -m pip install --upgrade --editable $script:RepoRoot"
        Write-Info "Dry run: would run nanobot as: $VenvDir\Scripts\python.exe -m nanobot"
    }
    if ($env:NANOBOT_SKIP_WIZARD -eq "1") {
        Write-Info "Dry run: would skip automatic setup because NANOBOT_SKIP_WIZARD=1."
    } else {
        Write-Info "Dry run: would run the setup wizard."
    }
    Write-Info "Dry run: no changes made."
    return
}

if (Test-VirtualEnv $Python) {
    Install-WithActivePython
} else {
    $Installed = $false

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Install-WithUv
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "uv tool install failed. Trying the next isolated install method..."
        }
    }

    if (-not $Installed -and (Get-Command pipx -ErrorAction SilentlyContinue)) {
        Install-WithPipx
        $Installed = $script:LastInstallSucceeded
        if (-not $Installed) {
            Write-Info "pipx install failed. Trying the managed virtual environment..."
        }
    }

    if (-not $Installed) {
        Write-Info "Using a dedicated virtual environment to avoid system pip."
        Install-WithManagedVenv
    }
}

Write-Info "Installed nanobot:"
Invoke-Nanobot @("--version")
if ($LASTEXITCODE -ne 0) {
    Fail "nanobot was installed, but the command could not be started."
}

if ($env:NANOBOT_SKIP_WIZARD -eq "1") {
    Write-Info "Skipping automatic setup because NANOBOT_SKIP_WIZARD=1."
    Write-Info "Run this later: $(Get-NanobotCommand) onboard"
    return
}

Write-Info "Starting setup wizard..."
Invoke-Nanobot @("onboard", "--wizard")
if ($LASTEXITCODE -ne 0) {
    Fail "Setup wizard did not complete."
}

Write-Info "Done. Try: $(Get-NanobotCommand) agent -m `"Hello!`""
