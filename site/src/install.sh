#!/bin/sh
# AWOS installer: https://awos.tirramind.com/install.sh
#
#   curl -fsSL https://awos.tirramind.com/install.sh | sh
#
# What it does, in order of preference:
#   1. uv is on PATH     -> uv tool install awos  (uv supplies Python >= 3.11 if needed)
#   2. pipx is on PATH   -> pipx install awos     (needs a Python >= 3.11 on PATH)
#   3. neither           -> says so, installs uv with its official installer
#                           (https://astral.sh/uv/install.sh, no shell rc edits), then 1.
#
# It never uses sudo and never edits your shell rc files. If the `awos`
# command ends up in a directory that is not on PATH, it prints the line to add.
# Running it again upgrades/reinstalls in place.
#
# Environment:
#   AWOS_REF=<tag|branch|sha>   git ref to install (default v0.1.0)
#   AWOS_REPO=<git url>         repository (default github.com/savabs/AWOS_coding_agent-)
#   AWOS_INSTALL_DRY_RUN=1      print every action instead of doing it
#   AWOS_NO_UV_INSTALL=1        never install uv; fail with instructions instead
#
# The whole script is one function called on the last line, so a truncated
# download runs nothing.

main() {
    set -eu

    AWOS_REF="${AWOS_REF:-v0.1.0}"
    AWOS_REPO="${AWOS_REPO:-https://github.com/savabs/AWOS_coding_agent-}"
    DRY_RUN="${AWOS_INSTALL_DRY_RUN:-0}"
    SPEC="awos @ git+${AWOS_REPO}@${AWOS_REF}"
    UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

    say "Installing AWOS (${AWOS_REF}) from ${AWOS_REPO}"
    if [ "$DRY_RUN" = "1" ]; then
        say "Dry run: printing actions only, nothing will be changed."
    fi

    check_platform
    check_not_root

    if ! has git; then
        die "git is required (AWOS installs from git and works on git repos)." \
            "Install git (macOS: xcode-select --install; Debian/Ubuntu: apt install git), then re-run."
    fi

    BIN_DIR=""
    if has uv; then
        install_with_uv uv
    elif has pipx && PY="$(find_python)"; then
        install_with_pipx "$PY"
    else
        if has pipx; then
            say "pipx found, but no Python >= 3.11 on PATH; using uv instead (it can provide Python)."
        else
            say "Neither uv nor pipx found."
        fi
        UV="$(bootstrap_uv)"
        install_with_uv "$UV"
    fi

    path_hint "$BIN_DIR"
    say ""
    if [ "$DRY_RUN" = "1" ]; then
        say "Dry run complete: nothing was installed."
    else
        say "AWOS is installed. Next: cd into a git repo with a failing test and start it."
    fi
    say "Run: awos"
}

say() { printf '%s\n' "$*"; }
err() { printf 'awos-install: error: %s\n' "$*" >&2; }

die() {
    err "$1"
    shift
    for line in "$@"; do printf '  %s\n' "$line" >&2; done
    exit 1
}

has() { command -v "$1" >/dev/null 2>&1; }

# run CMD...: execute, or print it in dry-run mode.
run() {
    if [ "$DRY_RUN" = "1" ]; then
        say "+ $*"
        return 0
    fi
    "$@"
}

check_platform() {
    os="$(uname -s 2>/dev/null || echo unknown)"
    case "$os" in
        Darwin|Linux) ;;
        *) die "unsupported OS '$os'. AWOS supports macOS and Linux." \
               "On Windows, use WSL2 and run this installer inside it." ;;
    esac
}

check_not_root() {
    uid="$(id -u 2>/dev/null || echo 1000)"
    if [ "$uid" = "0" ]; then
        say "Note: running as root. AWOS installs per user; you probably want to run this as your normal user (no sudo)."
    fi
}

# Print the first python3 >= 3.11 on PATH; fail if there is none.
find_python() {
    for cand in python3.13 python3.12 python3.11 python3 python; do
        if has "$cand" && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            command -v "$cand"
            return 0
        fi
    done
    return 1
}

install_with_uv() {
    uv_cmd="$1"
    say "Using uv: uv tool install (Python >= 3.11; uv downloads one if needed)"
    run "$uv_cmd" tool install --force --python ">=3.11" "$SPEC" \
        || die "uv tool install failed (see the output above)." \
               "Check your network and that ref '${AWOS_REF}' exists, then re-run."
    if [ "$DRY_RUN" = "1" ]; then
        BIN_DIR="$("$uv_cmd" tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
    else
        BIN_DIR="$("$uv_cmd" tool dir --bin 2>/dev/null || true)"
    fi
    [ -n "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"
}

install_with_pipx() {
    py="$1"
    say "Using pipx with $py"
    run pipx install --force --python "$py" "$SPEC" \
        || die "pipx install failed (see the output above)." \
               "Check your network and that ref '${AWOS_REF}' exists, then re-run."
    BIN_DIR="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    [ -n "$BIN_DIR" ] || BIN_DIR="$HOME/.local/bin"
}

# Install uv via its official installer, without touching shell rc files.
# Prints the path of the uv binary on stdout (all messages go to stderr).
bootstrap_uv() {
    if [ "${AWOS_NO_UV_INSTALL:-0}" = "1" ]; then
        die "AWOS_NO_UV_INSTALL=1 and neither uv nor pipx (with Python >= 3.11) is available." \
            "Install uv (https://docs.astral.sh/uv/) or pipx, then re-run."
    fi
    if ! has curl; then
        die "curl is required to install uv." \
            "Install uv yourself (https://docs.astral.sh/uv/getting-started/installation/), then re-run."
    fi
    {
        say "About to install uv (a Python package manager from Astral) with its official installer:"
        say "    curl -LsSf ${UV_INSTALLER_URL} | env UV_NO_MODIFY_PATH=1 sh"
        say "It goes into your home directory (~/.local/bin), needs no sudo, and edits no shell files."
    } >&2
    if [ "$DRY_RUN" = "1" ]; then
        say "+ curl -LsSf ${UV_INSTALLER_URL} | env UV_NO_MODIFY_PATH=1 sh" >&2
        printf '%s\n' "${XDG_BIN_HOME:-$HOME/.local/bin}/uv"
        return 0
    fi
    curl -LsSf "$UV_INSTALLER_URL" | env UV_NO_MODIFY_PATH=1 sh >&2 \
        || die "the uv installer failed." "See https://docs.astral.sh/uv/getting-started/installation/"
    for cand in "${XDG_BIN_HOME:-}/uv" "$HOME/.local/bin/uv" "${CARGO_HOME:-$HOME/.cargo}/bin/uv"; do
        if [ -x "$cand" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    die "uv was installed but its binary was not found in ~/.local/bin." \
        "Open a new shell and re-run this installer."
}

# If DIR is not on PATH, print the line to add (we never edit rc files).
path_hint() {
    dir="$1"
    case ":${PATH}:" in
        *":${dir}:"*) return 0 ;;
    esac
    shell_name="$(basename "${SHELL:-sh}")"
    case "$shell_name" in
        zsh) rc="$HOME/.zshrc" ;;
        bash) rc="$HOME/.bashrc (or $HOME/.bash_profile on macOS)" ;;
        fish) rc="$HOME/.config/fish/config.fish" ;;
        *) rc="your shell's startup file" ;;
    esac
    say ""
    say "The awos command is in ${dir}, which is not on your PATH."
    say "This installer does not edit shell files. Add this line to ${rc} yourself:"
    if [ "$shell_name" = "fish" ]; then
        say "    fish_add_path \"${dir}\""
    else
        say "    export PATH=\"${dir}:\$PATH\""
    fi
    say "then open a new terminal (or run that line now)."
}

main "$@"
