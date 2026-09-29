#!/bin/sh
set -eu

# This installer installs a local nanobot checkout in editable mode, so the
# running command always reflects the source tree it was installed from.
package="nanobot-ai"
# The public repository URL. It enables the piped installer
# (curl -fsSL <url> | sh) without an explicit --git flag.
default_git_url="https://github.com/Mochi-Sora/MasonAgent"
dry_run="0"
nanobot_runner=""
nanobot_python=""
nanobot_bin=""
requested_repo=""
requested_git="${NANOBOT_INSTALL_GIT:-}"
repo_root=""
install_source=""

info() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

install_failure_hint() {
  printf '%s\n' "Error: could not install nanobot from $install_source." >&2
  printf '%s\n' "If pip mentioned externally-managed-environment, use uv, pipx, or a virtual environment instead of system pip." >&2
  printf '%s\n' "You can also run manually:" >&2
  printf '  %s\n' "uv tool install --force --upgrade --editable $repo_root" >&2
  printf '  %s\n' "$python_bin -m venv ~/.nanobot/venv" >&2
  printf '  %s\n' "~/.nanobot/venv/bin/python -m pip install --upgrade --editable $repo_root" >&2
  printf '%s\n' "Then start setup with:" >&2
  printf '  %s\n' "nanobot onboard --wizard" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: install.sh [--dry-run] [--repo PATH] [--git URL]

By default this installs the nanobot checkout that contains this script in
editable mode. Run it from a clone:

  ./scripts/install.sh

Options:
  --repo PATH   Install a different local checkout in editable mode.
  --git URL     Clone (or update) the repository into ~/.nanobot/src and
                install from that clone. Required for piped installs.

Use --dry-run to print what would happen without installing or starting setup.

Environment:
  NANOBOT_SKIP_WIZARD=1   Skip the setup wizard.
  NANOBOT_VENV=/path      Use a different managed virtual environment.
  NANOBOT_SRC_DIR=/path   Clone into a different directory for --git.
  NANOBOT_INSTALL_GIT=URL Default repository for --git.
  PYTHON=python3          Use a specific Python interpreter.
EOF
}

is_checkout() {
  [ -n "$1" ] &&
    [ -f "$1/pyproject.toml" ] &&
    [ -f "$1/nanobot/__init__.py" ] &&
    grep -q '^name[[:space:]]*=[[:space:]]*"nanobot-ai"' "$1/pyproject.toml" 2>/dev/null
}

resolve_git_checkout() {
  git_url="$1"
  [ -n "${HOME:-}" ] || [ -n "${NANOBOT_SRC_DIR:-}" ] ||
    fail "Installing from a git URL needs HOME or NANOBOT_SRC_DIR to choose a clone directory"
  src_dir="${NANOBOT_SRC_DIR:-$HOME/.nanobot/src}"
  if [ "$dry_run" = "1" ]; then
    info "Dry run: would clone or update $git_url in $src_dir."
  else
    command -v git >/dev/null 2>&1 || fail "Installing from a git URL requires git on PATH"
    if [ -d "$src_dir/.git" ]; then
      info "Updating $src_dir from $git_url..."
      git -C "$src_dir" pull --ff-only >/dev/null 2>&1 ||
        fail "Could not update $src_dir. Remove that directory and rerun, or use --repo with a local checkout."
    else
      info "Cloning $git_url into $src_dir..."
      mkdir -p "$(dirname "$src_dir")"
      git clone --depth 1 "$git_url" "$src_dir" ||
        fail "Could not clone $git_url into $src_dir."
    fi
  fi
  repo_root="$src_dir"
  if [ "$dry_run" != "1" ] || [ -d "$repo_root" ]; then
    is_checkout "$repo_root" ||
      fail "The repository at $repo_root does not look like a nanobot checkout."
  fi
}

resolve_checkout() {
  if [ -n "$requested_repo" ]; then
    [ -d "$requested_repo" ] || fail "--repo directory not found: $requested_repo"
    candidate=$(CDPATH= cd -- "$requested_repo" && pwd)
    is_checkout "$candidate" ||
      fail "--repo $candidate is not a nanobot checkout (expected pyproject.toml with name = \"nanobot-ai\")"
    repo_root="$candidate"
    return 0
  fi

  if [ -n "$requested_git" ]; then
    resolve_git_checkout "$requested_git"
    return 0
  fi

  candidate=""
  case "$0" in
    */*)
      script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
      candidate=$(CDPATH= cd -- "$script_dir/.." 2>/dev/null && pwd) || candidate=""
      ;;
  esac
  if is_checkout "$candidate"; then
    repo_root="$candidate"
    return 0
  fi

  if [ -n "$default_git_url" ]; then
    resolve_git_checkout "$default_git_url"
    return 0
  fi

  fail "No nanobot checkout found. Run this script from a clone (./scripts/install.sh), pass --repo PATH, or pass --git URL."
}

find_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

python_is_virtual_env() {
  "$python_bin" - <<'PY'
import sys
raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)
PY
}

ensure_pip() {
  target_python="$1"
  if "$target_python" -m pip --version >/dev/null 2>&1; then
    return 0
  fi

  info "pip was not found for $target_python. Trying ensurepip..."
  "$target_python" -m ensurepip --upgrade >/dev/null 2>&1
}

run_nanobot() {
  case "$nanobot_runner" in
    uv)
      uv tool run --from "$package" nanobot "$@"
      ;;
    pipx)
      pipx run --spec "$package" nanobot "$@"
      ;;
    direct)
      "$nanobot_bin" "$@"
      ;;
    python)
      "$nanobot_python" -m nanobot "$@"
      ;;
    *)
      fail "nanobot was installed, but no runner was configured"
      ;;
  esac
}

nanobot_try_command() {
  case "$nanobot_runner" in
    uv)
      printf '%s\n' "uv tool run --from $package nanobot"
      ;;
    pipx)
      printf '%s\n' "pipx run --spec $package nanobot"
      ;;
    direct)
      printf '%s\n' "$nanobot_bin"
      ;;
    python)
      printf '%s\n' "$nanobot_python -m nanobot"
      ;;
  esac
}

is_fresh_nanobot_install() {
  [ -n "${HOME:-}" ] || return 1
  [ ! -e "$HOME/.nanobot/config.json" ]
}

has_browser_session() {
  if [ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]; then
    return 1
  fi
  if ! : 2>/dev/null < /dev/tty; then
    return 1
  fi

  case "$(uname -s)" in
    Darwin)
      command -v launchctl >/dev/null 2>&1 &&
        launchctl print "gui/$(id -u)" >/dev/null 2>&1
      ;;
    *)
      [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]
      ;;
  esac
}

install_with_active_python() {
  info "Detected an active virtual environment. Installing $install_source into it..."
  ensure_pip "$python_bin" || return 1
  "$python_bin" -m pip install --upgrade --editable "$repo_root" || return 1
  nanobot_runner="python"
  nanobot_python="$python_bin"
}

install_with_uv() {
  info "Installing $install_source with uv tool in editable mode..."
  uv tool install --python "$python_bin" --force --upgrade --editable "$repo_root" || return 1
  nanobot_runner="uv"
}

install_with_pipx() {
  info "Installing $install_source with pipx in editable mode..."
  pipx install --python "$python_bin" --force --editable "$repo_root" || return 1
  pipx_bin_dir=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
  [ -n "$pipx_bin_dir" ] || pipx_bin_dir="$HOME/.local/bin"
  if [ -x "$pipx_bin_dir/nanobot" ]; then
    nanobot_runner="direct"
    nanobot_bin="$pipx_bin_dir/nanobot"
  else
    nanobot_runner="pipx"
  fi
}

write_managed_wrapper() {
  bin_dir="${NANOBOT_BIN_DIR:-$HOME/.local/bin}"
  wrapper="$bin_dir/nanobot"
  mkdir -p "$bin_dir" || return 0

  if [ -e "$wrapper" ] && ! grep -q "Generated by nanobot installer" "$wrapper" 2>/dev/null; then
    info "Not updating $wrapper because it already exists."
    return 0
  fi

  cat > "$wrapper" <<EOF
#!/bin/sh
# Generated by nanobot installer.
exec "$nanobot_python" -m nanobot "\$@"
EOF
  chmod +x "$wrapper" || return 0

  if ! command -v nanobot >/dev/null 2>&1; then
    info "Installed a nanobot launcher at $wrapper."
    info "Add $bin_dir to PATH to run nanobot directly."
  fi
}

install_with_managed_venv() {
  [ -n "${HOME:-}" ] || fail "HOME is not set; cannot create a managed virtual environment"

  venv_dir="${NANOBOT_VENV:-$HOME/.nanobot/venv}"
  venv_python="$venv_dir/bin/python"

  if [ ! -x "$venv_python" ]; then
    info "Creating a dedicated virtual environment at $venv_dir..."
    mkdir -p "$(dirname "$venv_dir")"
    "$python_bin" -m venv "$venv_dir" || return 1
  fi

  "$venv_python" - <<'PY' >/dev/null 2>&1 || fail "The managed venv uses Python older than 3.11. Remove it or set NANOBOT_VENV to a new path."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

  info "Installing $install_source in $venv_dir..."
  ensure_pip "$venv_python" || return 1
  "$venv_python" -m pip install --upgrade --editable "$repo_root" || return 1

  nanobot_runner="python"
  nanobot_python="$venv_python"
  write_managed_wrapper
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev)
      info "--dev is the default now: this installer always installs a checkout in editable mode."
      ;;
    --repo)
      shift
      [ "$#" -gt 0 ] || fail "--repo requires a path"
      requested_repo="$1"
      ;;
    --repo=*)
      requested_repo="${1#--repo=}"
      ;;
    --git)
      shift
      [ "$#" -gt 0 ] || fail "--git requires a URL"
      requested_git="$1"
      ;;
    --git=*)
      requested_git="${1#--git=}"
      ;;
    --dry-run)
      dry_run="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

python_bin="${PYTHON:-}"

if [ -n "$python_bin" ]; then
  command -v "$python_bin" >/dev/null 2>&1 || fail "PYTHON=$python_bin was not found"
  "$python_bin" - <<'PY' >/dev/null 2>&1 || fail "nanobot requires Python 3.11 or newer"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
else
  python_bin="$(find_python)" || fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
fi

info "Using Python: $("$python_bin" --version 2>&1)"

resolve_checkout
install_source="the checkout at $repo_root"

if [ "$dry_run" = "1" ]; then
  info "Dry run: would install or upgrade nanobot from $install_source."
  if python_is_virtual_env; then
    info "Dry run: active virtual environment detected; would run: $python_bin -m pip install --upgrade --editable $repo_root"
    info "Dry run: would run nanobot as: $python_bin -m nanobot"
  elif command -v uv >/dev/null 2>&1; then
    info "Dry run: would run: uv tool install --python $python_bin --force --upgrade --editable $repo_root"
    info "Dry run: would run nanobot as: uv tool run --from $package nanobot"
  elif command -v pipx >/dev/null 2>&1; then
    info "Dry run: would run: pipx install --python $python_bin --force --editable $repo_root"
    info "Dry run: would run nanobot as: pipx run --spec $package nanobot"
  else
    venv_dir="${NANOBOT_VENV:-$HOME/.nanobot/venv}"
    info "Dry run: would create or reuse a dedicated virtual environment: $venv_dir"
    info "Dry run: would run: $venv_dir/bin/python -m pip install --upgrade --editable $repo_root"
    info "Dry run: would run nanobot as: $venv_dir/bin/python -m nanobot"
  fi
  if [ "${NANOBOT_SKIP_WIZARD:-}" = "1" ]; then
    info "Dry run: would skip automatic setup because NANOBOT_SKIP_WIZARD=1."
  else
    info "Dry run: would run the setup wizard."
  fi
  info "Dry run: no changes made."
  exit 0
fi

if python_is_virtual_env; then
  install_with_active_python || install_failure_hint
else
  installed="0"

  if command -v uv >/dev/null 2>&1; then
    if install_with_uv; then
      installed="1"
    else
      info "uv tool install failed. Trying the next isolated install method..."
    fi
  fi

  if [ "$installed" != "1" ] && command -v pipx >/dev/null 2>&1; then
    if install_with_pipx; then
      installed="1"
    else
      info "pipx install failed. Trying the managed virtual environment..."
    fi
  fi

  if [ "$installed" != "1" ]; then
    info "Using a dedicated virtual environment to avoid system pip."
    install_with_managed_venv || install_failure_hint
  fi
fi

info "Installed nanobot:"
run_nanobot --version

if [ "${NANOBOT_SKIP_WIZARD:-}" = "1" ]; then
  info "Skipping automatic setup because NANOBOT_SKIP_WIZARD=1."
  info "Run this later: $(nanobot_try_command) onboard"
  exit 0
fi

if [ -t 0 ]; then
  info "Starting setup wizard..."
  run_nanobot onboard --wizard
elif : 2>/dev/null < /dev/tty; then
  info "Starting setup wizard..."
  run_nanobot onboard --wizard < /dev/tty
else
  info "Skipping setup wizard because no interactive terminal is available."
  info "Run this later: $(nanobot_try_command) onboard --wizard"
fi

info "Done. Try: $(nanobot_try_command) agent -m \"Hello!\""
