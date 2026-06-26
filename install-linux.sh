#!/usr/bin/env bash
#
# autosuggest-cli — one-shot installer for managed ADI Linux hosts
# (Exceed TurboX / EDA-CAD farms where the login shell is csh/tcsh and
#  the system dotfiles are read-only).
#
# Run it ONCE.  It is idempotent — re-running only refreshes things and
# never duplicates dotfile entries.  After it finishes, every new login
# lands in a bash that already has Python, Perforce, ~/.local/bin on PATH,
# and the autosuggest hook active.  No further manual changes are required.
#
#   USAGE (from csh/tcsh or bash):
#       bash install-linux.sh
#
#   then either log out / log back in, or run:  exec bash --rcfile ~/.suggest_bashrc -i
#
#   OPTIONS (override the defaults via environment variables):
#       PY_MODULE        python module to load   (default: python/adi/3.12.2)
#       P4_MODULES       perforce modules         (default: perforce/adi/r19.1 p4v/adi/p4v-2024.1.2591061)
#       PKG_SOURCE       what pip installs        (default: this repo if run from a clone, else the git URL)
#       NO_AUTOLAUNCH=1  set up bash but do NOT auto-exec bash from csh login
#
#   KILL SWITCH (after install):
#       touch ~/.no_autosuggest   # disable auto-bash on next login
#       rm    ~/.no_autosuggest   # re-enable
#       exec tcsh -f              # drop to plain csh for one session
#
set -u

# ---- configuration (override via env) --------------------------------------
PY_MODULE="${PY_MODULE:-python/adi/3.12.2}"
P4_MODULES="${P4_MODULES:-perforce/adi/r19.1 p4v/adi/p4v-2024.1.2591061}"
GIT_URL="https://github.com/Adityasingh2811-ADI/autosuggest-cli.git"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd)"
if [ -z "${PKG_SOURCE:-}" ]; then
    if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        PKG_SOURCE="$SCRIPT_DIR"          # installing from a local clone
    else
        PKG_SOURCE="git+$GIT_URL"         # installing straight from GitHub
    fi
fi

BASHRC="$HOME/.suggest_bashrc"
CSHRC_USER="$HOME/.cshrc.user"
BEGIN="# >>> autosuggest-cli >>>"
END="# <<< autosuggest-cli <<<"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }
die()  { printf '\n  ERROR: %s\n' "$*" >&2; exit 1; }

echo
echo "=== autosuggest-cli : one-shot Linux install ==============================="
say  "package source : $PKG_SOURCE"
say  "python module  : $PY_MODULE"
say  "perforce       : $P4_MODULES"
echo

# ---- 1. initialise the Environment Modules system for bash -----------------
# bash does not get the `module` function for free; initialise it so we can
# `module load` python/perforce.  The sh-initialiser lives at $MODULESHOME.
if ! type module >/dev/null 2>&1; then
    if [ -n "${MODULESHOME:-}" ] && [ -f "$MODULESHOME/module.sh" ]; then
        # shellcheck disable=SC1090
        source "$MODULESHOME/module.sh"
    elif [ -f /usr/cadtools/bin/modules.dir/module.sh ]; then
        export MODULESHOME=/usr/cadtools/bin/modules.dir
        source "$MODULESHOME/module.sh"
    elif [ -f /etc/profile.d/modules.sh ]; then
        source /etc/profile.d/modules.sh
    fi
fi
if type module >/dev/null 2>&1; then
    ok "module command available"
else
    warn "could not initialise Environment Modules — continuing with system python"
fi

# ---- 2. load python (needed for the 3.10+ engine) --------------------------
if type module >/dev/null 2>&1; then
    module load $PY_MODULE >/dev/null 2>&1 && ok "loaded $PY_MODULE" \
        || warn "could not load $PY_MODULE — using whatever python3 is on PATH"
fi

PYBIN="$(command -v python3 || true)"
[ -n "$PYBIN" ] || die "no python3 found on PATH"
PYVER="$("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)"
say "using python3 = $PYBIN ($PYVER)"
case "$PYVER" in
    3.1[0-9]|3.[2-9]*|[4-9].*) : ;;   # 3.10+ ok
    *) die "python $PYVER is too old (need 3.10+). Set PY_MODULE to a newer module." ;;
esac

# ---- 3. install the package into ~/.local ----------------------------------
say "installing the package (pip --user) ..."
"$PYBIN" -m pip install --user --upgrade --no-cache-dir "$PKG_SOURCE" \
    || die "pip install failed"
ok "package installed into ~/.local"

# make sure ~/.local/bin is on PATH for the rest of THIS script too
export PATH="$HOME/.local/bin:$PATH"
command -v suggest-hook >/dev/null 2>&1 \
    && ok "suggest-hook found at $(command -v suggest-hook)" \
    || warn "suggest-hook not on PATH yet (the dotfiles below will fix that at login)"

# ---- 4. write ~/.suggest_bashrc (we own this file) -------------------------
# Order matters:
#   1) user's normal bashrc      -> baseline environment
#   2) init Modules for bash     -> defines `module`
#   3) module load py + perforce -> python engine + p4 on PATH / P4* env
#   4) RE-ADD ~/.local/bin       -> module load rebuilds PATH and drops it,
#                                    so suggest-* must be re-added AFTER it
#   5) eval the hook             -> activate suggestions last
[ -f "$BASHRC" ] && cp -p "$BASHRC" "$BASHRC.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null
cat > "$BASHRC" <<EOF
# Generated by autosuggest-cli install-linux.sh — safe to edit, but re-running
# the installer will overwrite it (a timestamped .bak is kept).
[ -f ~/.bashrc ] && . ~/.bashrc

# 1) make the Modules system usable in bash
if ! type module >/dev/null 2>&1; then
    if [ -n "\${MODULESHOME:-}" ] && [ -f "\$MODULESHOME/module.sh" ]; then
        source "\$MODULESHOME/module.sh"
    elif [ -f /usr/cadtools/bin/modules.dir/module.sh ]; then
        export MODULESHOME=/usr/cadtools/bin/modules.dir
        source "\$MODULESHOME/module.sh"
    fi
fi

# 2) load python (engine) + perforce (p4 / P4* env)
module load $PY_MODULE $P4_MODULES >/dev/null 2>&1

# 3) module load rebuilds PATH and can drop ~/.local/bin — re-add it AFTER
export PATH="\$HOME/.local/bin:\$PATH"

# 4) activate the autosuggest hook (ghost-text accept, frecency Tab, next-steps)
if command -v suggest-hook >/dev/null 2>&1; then
    eval "\$(suggest-hook bash)"
fi
EOF
ok "wrote $BASHRC"

# ---- 5. write the csh bootstrap + auto-launch into ~/.cshrc.user -----------
# ~/.cshrc (root-owned) sources ~/.cshrc.user, which IS user-writable. We put
# an idempotent, marker-delimited block there. It only acts on interactive
# logins and self-disables if ~/.no_autosuggest exists (kill switch).
touch "$CSHRC_USER" 2>/dev/null || die "cannot write $CSHRC_USER"

# strip any previous block so re-runs don't duplicate
if grep -qF "$BEGIN" "$CSHRC_USER" 2>/dev/null; then
    tmp="$CSHRC_USER.tmp.$$"
    sed "/$(printf '%s' "$BEGIN" | sed 's/[][\\/.*^$]/\\&/g')/,/$(printf '%s' "$END" | sed 's/[][\\/.*^$]/\\&/g')/d" \
        "$CSHRC_USER" > "$tmp" && mv "$tmp" "$CSHRC_USER"
fi

if [ "${NO_AUTOLAUNCH:-0}" = "1" ]; then
    AUTOLAUNCH_BODY="    # auto-launch disabled (NO_AUTOLAUNCH=1); run 'suggest-start' manually
    alias suggest-start 'bash --rcfile ~/.suggest_bashrc -i'"
else
    AUTOLAUNCH_BODY="    alias suggest-start 'bash --rcfile ~/.suggest_bashrc -i'
    # auto-enter the hooked bash for interactive logins (kill switch: ~/.no_autosuggest)
    if ( \$?prompt && ! \$?AUTOSUGGEST_ACTIVE && ! -e ~/.no_autosuggest ) then
        setenv AUTOSUGGEST_ACTIVE 1
        exec bash --rcfile ~/.suggest_bashrc -i
    endif"
fi

cat >> "$CSHRC_USER" <<EOF
$BEGIN
# Managed by autosuggest-cli install-linux.sh. Edit between the markers only;
# re-running the installer regenerates this block.
if ( \$?prompt ) then
$AUTOLAUNCH_BODY
endif
$END
EOF
ok "updated $CSHRC_USER"

# ---- 6. done ---------------------------------------------------------------
echo
echo "=== install complete ======================================================="
say "Start using it now without logging out:"
say "    exec bash --rcfile ~/.suggest_bashrc -i"
echo
say "Or just open a new terminal / log in again — it is automatic."
say "Verify inside the new shell:"
say "    p4 info            # Perforce works (uses your existing ticket)"
say "    suggest-daemon status"
say "    suggest-hook bash | grep -c _autosuggest_accept   # >=2 = full hook"
echo
say "Disable any time:  touch ~/.no_autosuggest      (re-enable: rm ~/.no_autosuggest)"
say "Plain csh once:    exec tcsh -f"
echo
