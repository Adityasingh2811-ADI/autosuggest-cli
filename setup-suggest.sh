#!/usr/bin/env bash
#
# setup-suggest.sh — environment setup for autosuggest-cli's `suggest`.
#
# Detects your login shell and does the boring parts for you:
#   * makes sure the package is installed (pip --user) if it's missing,
#   * puts ~/.local/bin on PATH so `suggest`/`suggest-hook` resolve,
#   * installs the CORRECT shell hook for your shell (bash/zsh/tcsh),
#   * writes all of that permanently into your shell rc file (idempotently),
#   * seeds suggestions from your existing history (once).
#
# So you never have to hand-run `export PATH` / `set path` / `eval ...` again.
#
#   USAGE (run with bash, even from a tcsh/csh prompt):
#       bash setup-suggest.sh                 # auto-detect your login shell
#       bash setup-suggest.sh bash            # force a specific shell
#       bash setup-suggest.sh --no-import     # skip history seeding
#       bash setup-suggest.sh --no-install    # don't pip-install if missing
#
# Re-running is safe — it never duplicates rc-file entries.

set -u

# ---- parse arguments -------------------------------------------------------
FORCE_SHELL=""
DO_IMPORT=1
DO_INSTALL=1
for arg in "$@"; do
    case "$arg" in
        bash|zsh|tcsh|csh) FORCE_SHELL="$arg" ;;
        --no-import)       DO_IMPORT=0 ;;
        --no-install)      DO_INSTALL=0 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) printf 'setup-suggest: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

MARK_BEGIN="# >>> autosuggest-cli >>>"
MARK_END="# <<< autosuggest-cli <<<"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
GIT_URL="https://github.com/Adityasingh2811-ADI/autosuggest-cli.git"
IMPORT_SENTINEL="$HOME/.cli_autosuggest.imported"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf '\n  ERROR: %s\n' "$*" >&2; exit 1; }

echo
echo "=== autosuggest-cli : shell environment setup ============================="

# ---- 1. detect the shell ---------------------------------------------------
detect_shell() {
    if [ -n "$FORCE_SHELL" ]; then
        printf '%s' "$FORCE_SHELL"
        return
    fi
    # Prefer $SHELL (the login shell) — that is what we want to make permanent.
    case "$(basename "${SHELL:-}")" in
        bash) printf 'bash'; return ;;
        zsh)  printf 'zsh';  return ;;
        tcsh|csh) printf 'tcsh'; return ;;
    esac
    # Fallback: inspect the parent process that launched this script.
    local pname
    pname="$(ps -p "${PPID:-0}" -o comm= 2>/dev/null | sed 's/^-//')"
    case "$pname" in
        *zsh*)        printf 'zsh' ;;
        *tcsh*|*csh*) printf 'tcsh' ;;
        *)            printf 'bash' ;;
    esac
}

SHELL_KIND="$(detect_shell)"
[ "$SHELL_KIND" = "csh" ] && SHELL_KIND="tcsh"
ok "detected shell: $SHELL_KIND"

case "$SHELL_KIND" in
    bash) RCFILE="$HOME/.bashrc" ;;
    zsh)  RCFILE="$HOME/.zshrc"  ;;
    tcsh) RCFILE="$HOME/.tcshrc" ;;
    *)    die "unsupported shell: $SHELL_KIND" ;;
esac
say "rc file       : $RCFILE"

# ---- 2. find python --------------------------------------------------------
PYBIN="$(command -v python3 || command -v python || true)"
[ -n "$PYBIN" ] || die "no python found on PATH (need Python 3.10+)."

# ---- 3. make sure ~/.local/bin is on PATH for the rest of THIS script -------
LOCALBIN="$HOME/.local/bin"
export PATH="$LOCALBIN:$PATH"

# ---- 4. install the package if the commands are missing --------------------
if ! command -v suggest-hook >/dev/null 2>&1; then
    if [ "$DO_INSTALL" = "1" ]; then
        if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
            SRC="$SCRIPT_DIR"
        else
            SRC="git+$GIT_URL"
        fi
        say "installing the package (pip --user) from: $SRC"
        "$PYBIN" -m pip install --user --upgrade --no-cache-dir "$SRC" \
            || die "pip install failed"
        export PATH="$LOCALBIN:$PATH"
    else
        warn "suggest-hook not found and --no-install given; skipping install"
    fi
fi

if command -v suggest-hook >/dev/null 2>&1; then
    ok "suggest-hook found: $(command -v suggest-hook)"
else
    warn "suggest-hook still not on PATH; the rc block below will fix it at login"
fi

# ---- 5. build the rc block for this shell ----------------------------------
build_block() {
    printf '%s\n' "$MARK_BEGIN"
    printf '# Managed by setup-suggest.sh — edit between the markers only.\n'
    case "$SHELL_KIND" in
        bash)
            printf 'export PATH="$HOME/.local/bin:$PATH"\n'
            printf 'command -v suggest-hook >/dev/null 2>&1 && eval "$(suggest-hook bash)"\n'
            ;;
        zsh)
            printf 'export PATH="$HOME/.local/bin:$PATH"\n'
            printf 'command -v suggest-hook >/dev/null 2>&1 && eval "$(suggest-hook zsh)"\n'
            ;;
        tcsh)
            # csh/tcsh cannot parse $(...); it uses backticks. PATH must be set
            # (and rehashed) BEFORE suggest-hook can be found.
            printf 'if ( -d ~/.local/bin ) then\n'
            printf '    set path = ( $HOME/.local/bin $path )\n'
            printf '    rehash\n'
            printf 'endif\n'
            printf 'if ( -X suggest-hook ) eval `suggest-hook tcsh`\n'
            ;;
    esac
    printf '%s\n' "$MARK_END"
}

# ---- 6. write the block idempotently ---------------------------------------
touch "$RCFILE" 2>/dev/null || die "cannot write $RCFILE"
# strip any previous block first so re-runs never duplicate
if grep -qF "$MARK_BEGIN" "$RCFILE" 2>/dev/null; then
    tmp="$RCFILE.tmp.$$"
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        $0==b {insec=1; next}
        $0==e {insec=0; next}
        !insec {print}
    ' "$RCFILE" > "$tmp" && mv "$tmp" "$RCFILE"
fi
{ printf '\n'; build_block; } >> "$RCFILE"
ok "wrote autosuggest block into $RCFILE"

# ---- 7. seed history once --------------------------------------------------
if [ "$DO_IMPORT" = "1" ] && [ ! -f "$IMPORT_SENTINEL" ]; then
    if command -v suggest-import >/dev/null 2>&1; then
        say "seeding suggestions from your existing history ..."
        suggest-import >/dev/null 2>&1 && touch "$IMPORT_SENTINEL"
        ok "history imported (one-time)"
    fi
fi

# ---- 8. done — tell the user how to activate now ---------------------------
echo
echo "=== setup complete ========================================================"
say "Start using it now:"
say "    suggest                 # the helper window (works in any shell)"
echo
case "$SHELL_KIND" in
    bash) say "To load suggestions in your CURRENT shell:  source ~/.bashrc" ;;
    zsh)  say "To load suggestions in your CURRENT shell:  source ~/.zshrc"  ;;
    tcsh) say "To load suggestions in your CURRENT shell:  source ~/.tcshrc" ;;
esac
say "New terminals/logins pick it up automatically."
echo
