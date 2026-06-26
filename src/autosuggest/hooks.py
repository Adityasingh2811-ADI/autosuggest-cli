"""
Shell integration hooks — provides bash and PowerShell snippets that record
telemetry to the autosuggest daemon from the user's real shell.
"""

import os
import sys
from pathlib import Path

BASH_HOOK = r'''
# autosuggest-cli: telemetry + suggestions hook for bash
# Records each command to the autosuggest daemon and surfaces frecency-based
# completions plus next-step prompts in your real bash shell.
#
# Feature parity notes vs PowerShell:
#   * Telemetry recording ......... yes (Unix socket, TCP fallback)
#   * Accept top suggestion ....... Ctrl+F, and Right-arrow at end of line
#   * Frecency Tab completion ..... yes (programmable completion)
#   * Next-step suggestions ....... yes (printed after each command)
# Stock bash cannot render continuously-updating inline ghost text reliably
# (readline redraws the line after each key); use the zsh hook for true inline
# ghost text. Set AUTOSUGGEST_NO_NEXTSTEPS=1 to silence next-step output.
_autosuggest_last_hist=""

# Resolve socket path (XDG on Linux, TCP fallback on Windows/WSL)
_autosuggest_sock="${XDG_RUNTIME_DIR:-/tmp/autosuggest-$(id -u)}/autosuggest.sock"

# Resolve a python interpreter once per session
_autosuggest_python=""
_autosuggest_resolve_python() {
    if [ -n "$_autosuggest_python" ]; then
        return
    fi
    if command -v python3 &>/dev/null; then
        _autosuggest_python="python3"
    elif command -v python &>/dev/null; then
        _autosuggest_python="python"
    fi
}
_autosuggest_resolve_python

_autosuggest_ensure_daemon() {
    # Check if daemon is already listening (Unix socket or TCP fallback)
    if [[ -S "$_autosuggest_sock" ]]; then
        return
    fi
    (echo "" > /dev/tcp/127.0.0.1/19526) 2>/dev/null && return

    # Not running — start it detached
    if command -v suggest-daemon &>/dev/null; then
        suggest-daemon start </dev/null &>/dev/null &
        disown
    elif [ -n "$_autosuggest_python" ]; then
        "$_autosuggest_python" -m autosuggest.daemon start </dev/null &>/dev/null &
        disown
    fi
}

# Send a JSON payload to the daemon, fire-and-forget. Prefers the Unix socket
# (via socat when available, else a short python one-shot) and falls back to TCP.
_autosuggest_send() {
    local payload="$1"
    if [[ -S "$_autosuggest_sock" ]]; then
        if command -v socat &>/dev/null; then
            printf '%s' "$payload" | socat - UNIX-CONNECT:"$_autosuggest_sock" 2>/dev/null &
            disown 2>/dev/null
            return
        fi
        if [ -n "$_autosuggest_python" ]; then
            "$_autosuggest_python" -c "
import socket,sys
try:
    s=socket.socket(socket.AF_UNIX);s.settimeout(0.1)
    s.connect('$_autosuggest_sock');s.sendall(sys.stdin.buffer.read());s.close()
except Exception:
    pass
" <<< "$payload" 2>/dev/null &
            disown 2>/dev/null
            return
        fi
    fi
    # TCP fallback (works on WSL/Windows-hosted daemons and when no socket helper)
    if (exec 3<>/dev/tcp/127.0.0.1/19526) 2>/dev/null; then
        printf '%s' "$payload" >&3 2>/dev/null
        exec 3>&- 2>/dev/null
        return
    fi
    if [ -n "$_autosuggest_python" ]; then
        "$_autosuggest_python" -c "
import socket,sys
try:
    s=socket.socket();s.settimeout(0.1);s.connect(('127.0.0.1',19526))
    s.sendall(sys.stdin.buffer.read());s.close()
except Exception:
    pass
" <<< "$payload" 2>/dev/null &
        disown 2>/dev/null
    fi
}

# Query top frecency suggestions for a prefix. Prints one suggestion per line.
_autosuggest_query() {
    local prefix="$1"
    [ -z "$prefix" ] && return
    [ -z "$_autosuggest_python" ] && return
    "$_autosuggest_python" -m autosuggest.query "$prefix" "$PWD" 2>/dev/null
}

_autosuggest_hook() {
    local exit_status=$?
    local cmd
    cmd=$(HISTTIMEFORMAT='' history 1 | sed 's/^[ ]*[0-9]*[ ]*//')

    # Avoid recording duplicates from the same prompt cycle
    if [ "$cmd" = "$_autosuggest_last_hist" ]; then
        return
    fi
    _autosuggest_last_hist="$cmd"

    # Skip empty commands
    if [ -z "$cmd" ]; then
        return
    fi

    # Escape backslashes and double quotes for JSON
    local escaped_cmd="${cmd//\\/\\\\}"
    escaped_cmd="${escaped_cmd//\"/\\\"}"
    local escaped_cwd="${PWD//\\/\\\\}"
    escaped_cwd="${escaped_cwd//\"/\\\"}"

    local payload
    payload=$(printf '{"command":"%s","cwd":"%s","exit_status":%d}' "$escaped_cmd" "$escaped_cwd" "$exit_status")
    _autosuggest_send "$payload"

    # Show next-step suggestions for the command just run
    if [ -z "$AUTOSUGGEST_NO_NEXTSTEPS" ] && [ -n "$_autosuggest_python" ]; then
        local steps
        steps=$("$_autosuggest_python" -m autosuggest.nextsteps_cli "$cmd" "$PWD" 2>/dev/null)
        if [ -n "$steps" ]; then
            printf '\n  \033[36mNext steps:\033[0m\n'
            local i=1 line suggestion source
            while IFS=$'\t' read -r suggestion source _; do
                [ -z "$suggestion" ] && continue
                if [ -n "$source" ]; then
                    printf '  \033[1m[%d]\033[0m %-40s \033[33m(%s)\033[0m\n' "$i" "$suggestion" "$source"
                else
                    printf '  \033[1m[%d]\033[0m %-40s\n' "$i" "$suggestion"
                fi
                i=$((i + 1))
            done <<< "$steps"
            printf '\n'
        fi
    fi
}

# Accept the top frecency suggestion for the current line (Ctrl+F / Right-arrow).
_autosuggest_accept() {
    [ -z "$READLINE_LINE" ] && return
    local top
    top=$(_autosuggest_query "$READLINE_LINE" | head -n 1)
    if [ -n "$top" ] && [ "$top" != "$READLINE_LINE" ]; then
        READLINE_LINE="$top"
        READLINE_POINT=${#top}
    fi
}

# Right-arrow: accept suggestion when at end of line, else move cursor right.
_autosuggest_right() {
    if [ "$READLINE_POINT" -eq "${#READLINE_LINE}" ] && [ -n "$READLINE_LINE" ]; then
        _autosuggest_accept
    else
        READLINE_POINT=$((READLINE_POINT + 1))
    fi
}

# Frecency-aware Tab completion: merge daemon suggestions with default results.
# Suggestions from the engine are full command lines, but bash replaces only
# the current word. Strip the line-prefix before the current word from each
# suggestion so the completion extends the line instead of duplicating words.
_autosuggest_complete() {
    local line="${COMP_LINE:0:$COMP_POINT}"
    [ ${#line} -lt 2 ] && return
    [ -z "$_autosuggest_python" ] && return
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local before="${line%"$cur"}"
    local sugg
    while IFS= read -r sugg; do
        [ -z "$sugg" ] && continue
        case "$sugg" in
            "$line"*) COMPREPLY+=("${sugg#"$before"}") ;;
        esac
    done < <(_autosuggest_query "$line")
    [ ${#COMPREPLY[@]} -gt 0 ] && compopt -o nospace 2>/dev/null
}

# Start daemon on shell init (runs once per session, daemon persists across sessions)
_autosuggest_ensure_daemon

# Key bindings (interactive shells only)
if [[ $- == *i* ]]; then
    bind -x '"\C-f": _autosuggest_accept' 2>/dev/null
    bind -x '"\e[C": _autosuggest_right' 2>/dev/null
    # Frecency completion as the default completion for commands without one
    complete -D -F _autosuggest_complete -o bashdefault -o default 2>/dev/null
fi

# Prepend to PROMPT_COMMAND without duplicating
if [[ "${PROMPT_COMMAND}" != *"_autosuggest_hook"* ]]; then
    PROMPT_COMMAND="_autosuggest_hook;${PROMPT_COMMAND:-:}"
fi
'''

ZSH_HOOK = r'''
# autosuggest-cli: telemetry + inline ghost-text + next-steps hook for zsh
# zsh's line editor (zle) supports true inline ghost text via POSTDISPLAY,
# giving feature parity with the PowerShell predictor.
#   * Inline ghost text ........... yes (accept with Right-arrow or Ctrl+F)
#   * Frecency Tab completion ..... yes
#   * Next-step suggestions ....... yes (printed after each command)
# Set AUTOSUGGEST_NO_NEXTSTEPS=1 to silence next-step output.

_autosuggest_sock="${XDG_RUNTIME_DIR:-/tmp/autosuggest-$(id -u)}/autosuggest.sock"

_autosuggest_python=""
if command -v python3 &>/dev/null; then
    _autosuggest_python="python3"
elif command -v python &>/dev/null; then
    _autosuggest_python="python"
fi

_autosuggest_ensure_daemon() {
    if [[ -S "$_autosuggest_sock" ]]; then
        return
    fi
    (echo "" > /dev/tcp/127.0.0.1/19526) 2>/dev/null && return
    if command -v suggest-daemon &>/dev/null; then
        suggest-daemon start </dev/null &>/dev/null &!
    elif [[ -n "$_autosuggest_python" ]]; then
        "$_autosuggest_python" -m autosuggest.daemon start </dev/null &>/dev/null &!
    fi
}

_autosuggest_send() {
    local payload="$1"
    if [[ -S "$_autosuggest_sock" ]]; then
        if command -v socat &>/dev/null; then
            printf '%s' "$payload" | socat - UNIX-CONNECT:"$_autosuggest_sock" 2>/dev/null &!
            return
        fi
        if [[ -n "$_autosuggest_python" ]]; then
            "$_autosuggest_python" -c "
import socket,sys
try:
    s=socket.socket(socket.AF_UNIX);s.settimeout(0.1)
    s.connect('$_autosuggest_sock');s.sendall(sys.stdin.buffer.read());s.close()
except Exception:
    pass
" <<< "$payload" 2>/dev/null &!
            return
        fi
    fi
    if (exec 3<>/dev/tcp/127.0.0.1/19526) 2>/dev/null; then
        printf '%s' "$payload" >&3 2>/dev/null
        exec 3>&- 2>/dev/null
        return
    fi
    if [[ -n "$_autosuggest_python" ]]; then
        "$_autosuggest_python" -c "
import socket,sys
try:
    s=socket.socket();s.settimeout(0.1);s.connect(('127.0.0.1',19526))
    s.sendall(sys.stdin.buffer.read());s.close()
except Exception:
    pass
" <<< "$payload" 2>/dev/null &!
    fi
}

_autosuggest_query() {
    local prefix="$1"
    [[ -z "$prefix" || -z "$_autosuggest_python" ]] && return
    "$_autosuggest_python" -m autosuggest.query "$prefix" "$PWD" 2>/dev/null
}

# --- Telemetry + next-steps after each command (precmd hook) ---
_autosuggest_precmd() {
    local exit_status=$?
    local cmd
    cmd=$(fc -ln -1 2>/dev/null)
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"  # ltrim
    if [[ -z "$cmd" || "$cmd" == "$_autosuggest_last_hist" ]]; then
        return
    fi
    _autosuggest_last_hist="$cmd"

    local escaped_cmd="${cmd//\\/\\\\}"
    escaped_cmd="${escaped_cmd//\"/\\\"}"
    local escaped_cwd="${PWD//\\/\\\\}"
    escaped_cwd="${escaped_cwd//\"/\\\"}"
    local payload
    payload=$(printf '{"command":"%s","cwd":"%s","exit_status":%d}' "$escaped_cmd" "$escaped_cwd" "$exit_status")
    _autosuggest_send "$payload"

    if [[ -z "$AUTOSUGGEST_NO_NEXTSTEPS" && -n "$_autosuggest_python" ]]; then
        local steps
        steps=$("$_autosuggest_python" -m autosuggest.nextsteps_cli "$cmd" "$PWD" 2>/dev/null)
        if [[ -n "$steps" ]]; then
            print -P "\n  %F{cyan}Next steps:%f"
            local i=1 suggestion source rest
            while IFS=$'\t' read -r suggestion source rest; do
                [[ -z "$suggestion" ]] && continue
                if [[ -n "$source" ]]; then
                    printf '  \033[1m[%d]\033[0m %-40s \033[33m(%s)\033[0m\n' "$i" "$suggestion" "$source"
                else
                    printf '  \033[1m[%d]\033[0m %-40s\n' "$i" "$suggestion"
                fi
                i=$((i + 1))
            done <<< "$steps"
            printf '\n'
        fi
    fi
}

# --- Inline ghost text via zle POSTDISPLAY ---
_autosuggest_ghost=""

_autosuggest_render() {
    POSTDISPLAY=""
    _autosuggest_ghost=""
    if [[ -z "$BUFFER" || $CURSOR -ne ${#BUFFER} || ${#BUFFER} -lt 2 ]]; then
        return
    fi
    local top
    top=$(_autosuggest_query "$BUFFER" | head -n 1)
    if [[ -n "$top" && "$top" != "$BUFFER" && "$top" == "$BUFFER"* ]]; then
        _autosuggest_ghost="$top"
        POSTDISPLAY="${top#$BUFFER}"
    fi
}

_autosuggest_self_insert() {
    zle .self-insert
    _autosuggest_render
}

_autosuggest_backward_delete() {
    zle .backward-delete-char
    _autosuggest_render
}

_autosuggest_accept_or_right() {
    if [[ -n "$_autosuggest_ghost" && $CURSOR -eq ${#BUFFER} ]]; then
        BUFFER="$_autosuggest_ghost"
        CURSOR=${#BUFFER}
        POSTDISPLAY=""
        _autosuggest_ghost=""
    else
        zle .forward-char
    fi
}

_autosuggest_accept_full() {
    if [[ -n "$_autosuggest_ghost" ]]; then
        BUFFER="$_autosuggest_ghost"
        CURSOR=${#BUFFER}
        POSTDISPLAY=""
        _autosuggest_ghost=""
    fi
}

# --- Frecency-aware completion ---
_autosuggest_complete() {
    local cur="${LBUFFER}"
    [[ ${#cur} -lt 2 || -z "$_autosuggest_python" ]] && return
    local -a matches
    matches=("${(@f)$(_autosuggest_query "$cur")}")
    if [[ ${#matches[@]} -gt 0 ]]; then
        compadd -Q -- "${matches[@]}"
    fi
}

_autosuggest_ensure_daemon
_autosuggest_last_hist=""

autoload -Uz add-zsh-hook 2>/dev/null
add-zsh-hook precmd _autosuggest_precmd 2>/dev/null

if [[ -o interactive ]]; then
    zle -N _autosuggest_self_insert
    zle -N _autosuggest_backward_delete
    zle -N _autosuggest_accept_or_right
    zle -N _autosuggest_accept_full
    # Route every key currently bound to self-insert through our widget so the
    # ghost re-renders on each keystroke.
    bindkey -M main self-insert _autosuggest_self_insert 2>/dev/null
    bindkey '^?' _autosuggest_backward_delete   # Backspace
    bindkey '^H' _autosuggest_backward_delete
    bindkey '^[[C' _autosuggest_accept_or_right # Right arrow
    bindkey '^F' _autosuggest_accept_full       # Ctrl+F
fi
'''


POWERSHELL_HOOK = r'''
# autosuggest-cli: telemetry + ghost-text + next-steps hook for PowerShell
# Provides frecency-based inline suggestions, Tab completions, and next-step prompts.

function global:_AutosuggestEnsureDaemon {
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        if ($tcp.ConnectAsync("127.0.0.1", 19526).Wait(200)) {
            $tcp.Close()
            return
        }
    } catch {}

    $daemonCmd = Get-Command suggest-daemon -ErrorAction SilentlyContinue
    if ($daemonCmd) {
        Start-Process -FilePath $daemonCmd.Source -ArgumentList "start" -WindowStyle Hidden
    } else {
        $py = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
              elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
              else { $null }
        if ($py) {
            Start-Process -FilePath $py -ArgumentList "-m", "autosuggest.daemon", "start" -WindowStyle Hidden
        }
    }
}

# --- Enable VT (ANSI escape) processing for ghost text on conhost ---
# Windows Terminal supports VT natively; conhost needs it enabled via kernel32.
try {
    $null = [Console]::OutputEncoding
    if (-not $env:WT_SESSION) {
        # Running in legacy conhost — enable ENABLE_VIRTUAL_TERMINAL_PROCESSING
        $sig = '[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);'
        $sig2 = '[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);'
        $sig3 = '[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int id);'
        $k32 = Add-Type -MemberDefinition "$sig`n$sig2`n$sig3" -Name "K32VT" -Namespace "Autosuggest" -PassThru -ErrorAction SilentlyContinue
        if ($k32) {
            $hOut = $k32::GetStdHandle(-11)
            $mode = 0
            $null = $k32::GetConsoleMode($hOut, [ref]$mode)
            $null = $k32::SetConsoleMode($hOut, $mode -bor 4)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        }
    }
} catch {}

# --- Global state ---
$global:_AutosuggestLastHistId = -1
$global:_AutosuggestLastNextStepId = -1
$global:_AutosuggestNextSteps = @()
$global:_AutosuggestDbPath = Join-Path $env:USERPROFILE ".cli_autosuggest.db"
$global:_AutosuggestPython = $null

function global:_AutosuggestGetPython {
    if ($global:_AutosuggestPython) { return $global:_AutosuggestPython }
    $global:_AutosuggestPython = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
          elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" }
          else { $null }
    return $global:_AutosuggestPython
}

function global:_AutosuggestQuery {
    param([string]$Prefix)
    if (-not (Test-Path $global:_AutosuggestDbPath)) { return @() }
    $py = _AutosuggestGetPython
    if (-not $py) { return @() }
    $results = & $py -m autosuggest.query $Prefix $PWD.Path 2>$null
    return $results
}

function global:_AutosuggestSendTelemetry {
    $lastEntry = Get-History -Count 1
    if (-not $lastEntry) { return }
    if ($lastEntry.Id -eq $global:_AutosuggestLastHistId) { return }
    $global:_AutosuggestLastHistId = $lastEntry.Id

    $cmd = $lastEntry.CommandLine
    $cwd = $PWD.Path
    $exitStatus = if ($lastEntry.ExecutionStatus -eq 'Completed') { 0 } else { 1 }

    $cmd = $cmd -replace '\\', '\\' -replace '"', '\"'
    $cwd = $cwd -replace '\\', '\\' -replace '"', '\"'

    $payload = "{`"command`":`"$cmd`",`"cwd`":`"$cwd`",`"exit_status`":$exitStatus}"

    $null = Start-Job -ScriptBlock {
        param($p)
        try {
            $tcp = [System.Net.Sockets.TcpClient]::new()
            if ($tcp.ConnectAsync("127.0.0.1", 19526).Wait(100)) {
                $stream = $tcp.GetStream()
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($p)
                $stream.Write($bytes, 0, $bytes.Length)
                $tcp.Close()
            }
        } catch {}
    } -ArgumentList $payload
}

# Start daemon on shell init
_AutosuggestEnsureDaemon

# --- Ghost-text inline prediction ---
$_asPS7Plus = $PSVersionTable.PSVersion.Major -ge 7
$_asPSRL = Get-Module PSReadLine -ErrorAction SilentlyContinue
$_asHasPrediction = $_asPS7Plus -and $_asPSRL -and ($_asPSRL.Version -ge [Version]"2.2.6")
$_asGhostEnabled = $false

if ($_asHasPrediction) {
    # PS 7+ with PSReadLine 2.2.6+: register custom ICommandPredictor for frecency-based ghost text
    try {
        $_asPredictorSource = @"
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Management.Automation.Subsystem;
using System.Management.Automation.Subsystem.Prediction;
using System.Threading;

public class AutosuggestPredictor : ICommandPredictor
{
    private readonly Guid _id = new Guid("7f3a9c1e-4b2d-4e8f-a6c0-1d2e3f4a5b6c");
    public string Name => "autosuggest-cli";
    public string Description => "Frecency-based CLI suggestions";
    public Guid Id => _id;

    private static string _cachedPrefix = "";
    private static string[] _cachedResults = Array.Empty<string>();
    private static DateTime _cacheTime = DateTime.MinValue;
    private static readonly TimeSpan CacheTTL = TimeSpan.FromSeconds(2);
    private static readonly string _python;

    static AutosuggestPredictor()
    {
        var paths = new[] { "python3", "python" };
        foreach (var p in paths)
        {
            try
            {
                var psi = new ProcessStartInfo(p, "--version")
                {
                    RedirectStandardOutput = true,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                using var proc = Process.Start(psi);
                if (proc != null && proc.WaitForExit(1000) && proc.ExitCode == 0)
                {
                    _python = p;
                    break;
                }
            }
            catch { }
        }
    }

    public SuggestionPackage GetSuggestion(
        PredictionClient client, PredictionContext context, CancellationToken token)
    {
        if (_python == null) return default;

        string input = context.InputAst.Extent.Text;
        if (string.IsNullOrWhiteSpace(input) || input.Length < 2)
            return default;

        string[] results = GetCachedOrQuery(input);
        if (results.Length == 0) return default;

        var suggestions = new List<PredictiveSuggestion>();
        foreach (var r in results)
        {
            if (!string.IsNullOrEmpty(r) && r.Length > input.Length
                && r.StartsWith(input, StringComparison.OrdinalIgnoreCase))
            {
                suggestions.Add(new PredictiveSuggestion(r));
            }
        }
        return suggestions.Count > 0
            ? new SuggestionPackage(suggestions)
            : default;
    }

    private static string[] GetCachedOrQuery(string prefix)
    {
        if (_cachedResults.Length > 0
            && (DateTime.Now - _cacheTime) < CacheTTL
            && prefix.StartsWith(_cachedPrefix, StringComparison.OrdinalIgnoreCase))
        {
            return _cachedResults;
        }

        try
        {
            string cwd = Environment.CurrentDirectory;
            var psi = new ProcessStartInfo(_python)
            {
                Arguments = $"-m autosuggest.query \"{prefix.Replace("\"", "\\\"")}\" \"{cwd.Replace("\"", "\\\"")}\"",
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using var proc = Process.Start(psi);
            if (proc != null && proc.WaitForExit(150))
            {
                string output = proc.StandardOutput.ReadToEnd();
                var lines = output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                if (lines.Length > 0)
                {
                    _cachedPrefix = prefix;
                    _cachedResults = lines;
                    _cacheTime = DateTime.Now;
                    return lines;
                }
            }
        }
        catch { }

        return _cachedResults;
    }

    public bool CanAcceptFeedback(PredictionClient client, PredictorFeedbackKind kind) => false;
    public void OnSuggestionAccepted(PredictionClient client, uint session, string acceptedSuggestion) { }
    public void OnSuggestionDisplayed(PredictionClient client, uint session, int countOrIndex) { }
    public void OnCommandLineAccepted(PredictionClient client, IReadOnlyList<string> history) { }
    public void OnCommandLineExecuted(PredictionClient client, string commandLine, bool success) { }
}
"@
        Add-Type -TypeDefinition $_asPredictorSource -Language CSharp -ErrorAction Stop
        $predictor = [AutosuggestPredictor]::new()
        [System.Management.Automation.Subsystem.SubsystemManager]::RegisterSubsystem(
            [System.Management.Automation.Subsystem.Prediction.ICommandPredictor], $predictor)
        Set-PSReadLineOption -PredictionSource HistoryAndPlugin
        Set-PSReadLineOption -PredictionViewStyle InlineView
        $global:_asGhostEnabled = $true
    } catch {
        # Predictor registration failed — try fallback below
    }
}

if (-not $global:_asGhostEnabled -and $_asPSRL) {
    # PS 5.1 fallback: use PSReadLine's built-in history prediction if PSReadLine >= 2.2
    if ($_asPSRL.Version -ge [Version]"2.2.0") {
        try {
            Set-PSReadLineOption -PredictionSource History
            Set-PSReadLineOption -PredictionViewStyle InlineView
            $global:_asGhostEnabled = $true
        } catch {}
    }

    if (-not $global:_asGhostEnabled) {
        # PSReadLine < 2.2: no native inline ghost. Use a custom renderer via
        # console VT sequences drawn after the cursor, erased on next keystroke.
        # PS 5.1 doesn't support `e — use [char]27 for ESC.
        $global:_asGhostText = ""
        $global:_asGhostVisible = $false
        $global:_asESC = [char]27

        function global:_AutosuggestClearGhost {
            if ($global:_asGhostVisible) {
                $e = $global:_asESC
                # Save pos, erase to EOL, restore pos
                [Console]::Write("$e[s$e[K$e[u")
                $global:_asGhostVisible = $false
                $global:_asGhostText = ""
            }
        }

        function global:_AutosuggestRenderGhost {
            $line = $null; $cursor = $null
            [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)

            _AutosuggestClearGhost

            if ($cursor -ne $line.Length -or $line.Length -lt 2) { return }

            $suggestions = _AutosuggestQuery -Prefix $line
            if (-not $suggestions) { return }
            $top = if ($suggestions -is [array]) { $suggestions[0] } else { $suggestions }

            if ($top -and $top.Length -gt $line.Length -and $top.StartsWith($line, [System.StringComparison]::OrdinalIgnoreCase)) {
                $ghost = $top.Substring($line.Length)
                $global:_asGhostText = $top
                $global:_asGhostVisible = $true
                $e = $global:_asESC
                # Save cursor, dark gray text, reset color, restore cursor
                [Console]::Write("$e[s$e[90m$ghost$e[0m$e[u")
            }
        }

        function global:_AutosuggestAcceptGhost {
            if ($global:_asGhostVisible -and $global:_asGhostText) {
                $line = $null; $cursor = $null
                [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
                _AutosuggestClearGhost
                [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $global:_asGhostText)
                [Microsoft.PowerShell.PSConsoleReadLine]::SetCursorPosition($global:_asGhostText.Length)
            } else {
                [Microsoft.PowerShell.PSConsoleReadLine]::ForwardChar()
            }
        }

        $global:_asGhostEnabled = $true
    }
}

# --- PSReadLine key handlers ---
if (Get-Module PSReadLine) {
    # Ctrl+F: replace current line with top frecency suggestion (all PS versions)
    Set-PSReadLineKeyHandler -Key "Ctrl+f" -BriefDescription "AutosuggestAccept" -Description "Accept top autosuggest completion" -ScriptBlock {
        $line = $null; $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        if ($line.Length -gt 0) {
            $suggestions = _AutosuggestQuery -Prefix $line
            if ($suggestions -and $suggestions.Count -gt 0) {
                $top = if ($suggestions -is [array]) { $suggestions[0] } else { $suggestions }
                [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $top)
                [Microsoft.PowerShell.PSConsoleReadLine]::SetCursorPosition($top.Length)
            }
        }
    }

    # Right arrow: accept ghost suggestion if at end of line
    Set-PSReadLineKeyHandler -Key "RightArrow" -BriefDescription "AutosuggestAcceptInline" -Description "Accept inline suggestion or move cursor" -ScriptBlock {
        $line = $null; $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        if ($cursor -eq $line.Length -and $line.Length -gt 0) {
            if ($global:_asGhostVisible -and $global:_asGhostText) {
                _AutosuggestAcceptGhost
            } elseif ($_asHasPrediction) {
                [Microsoft.PowerShell.PSConsoleReadLine]::AcceptSuggestion()
            } else {
                [Microsoft.PowerShell.PSConsoleReadLine]::ForwardChar()
            }
        } else {
            [Microsoft.PowerShell.PSConsoleReadLine]::ForwardChar()
        }
    }

    # PS 5.1 custom ghost text: hook SelfInsert and Backspace to render ghost after each change
    if ($global:_asGhostEnabled -and -not $_asHasPrediction) {
        # After each character insertion, re-render ghost text
        Set-PSReadLineKeyHandler -Key "a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q","r","s","t","u","v","w","x","y","z","A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","0","1","2","3","4","5","6","7","8","9","Spacebar","OemMinus","OemPeriod","Oem2","Oem5" -BriefDescription "AutosuggestSelfInsert" -Description "Insert char and show ghost" -ScriptBlock {
            param($key, $arg)
            [Microsoft.PowerShell.PSConsoleReadLine]::SelfInsert($key, $arg)
            _AutosuggestRenderGhost
        }

        Set-PSReadLineKeyHandler -Key "Backspace" -BriefDescription "AutosuggestBackspace" -Description "Delete char and update ghost" -ScriptBlock {
            param($key, $arg)
            _AutosuggestClearGhost
            [Microsoft.PowerShell.PSConsoleReadLine]::BackwardDeleteChar($key, $arg)
            _AutosuggestRenderGhost
        }

        Set-PSReadLineKeyHandler -Key "Escape" -BriefDescription "AutosuggestEscape" -Description "Clear ghost and revert" -ScriptBlock {
            param($key, $arg)
            _AutosuggestClearGhost
            [Microsoft.PowerShell.PSConsoleReadLine]::RevertLine($key, $arg)
        }
    }
}

# --- Improved Tab completion with frecency ---
$global:_AutosuggestOriginalTabExpansion2 = if (Test-Path Function:\TabExpansion2) {
    Get-Content Function:\TabExpansion2
} else { $null }

function global:TabExpansion2 {
    param($inputScript, $cursorColumn)
    $results = $null
    if ($global:_AutosuggestOriginalTabExpansion2) {
        $results = & ([scriptblock]::Create($global:_AutosuggestOriginalTabExpansion2)) $inputScript $cursorColumn
    }
    if (-not $results) {
        $results = [System.Management.Automation.CommandCompletion]::CompleteInput($inputScript, $cursorColumn, $null)
    }

    if ($inputScript.Length -ge 2) {
        $prefix = $inputScript.Substring(0, $cursorColumn)
        $suggestions = _AutosuggestQuery -Prefix $prefix
        if ($suggestions) {
            $items = if ($suggestions -is [array]) { $suggestions } else { @($suggestions) }
            $existingSet = [System.Collections.Generic.HashSet[string]]::new(
                [StringComparer]::OrdinalIgnoreCase)
            foreach ($m in $results.CompletionMatches) {
                $null = $existingSet.Add($m.CompletionText)
            }

            # Prepend for full-command context, append for arguments
            $isFullCommand = -not $prefix.Contains(' ')
            $insertIdx = if ($isFullCommand) { 0 } else { $results.CompletionMatches.Count }

            foreach ($s in $items) {
                if (-not $existingSet.Contains($s)) {
                    $cr = [System.Management.Automation.CompletionResult]::new(
                        $s, $s, 'ParameterValue', "[frecency] $s")
                    $results.CompletionMatches.Insert($insertIdx, $cr)
                    $insertIdx++
                }
            }
        }
    }
    return $results
}

# --- Prompt override: telemetry + next-steps display ---
$global:_AutosuggestOriginalPrompt = if (Test-Path Function:\prompt) {
    Get-Content Function:\prompt
} else { $null }

function global:prompt {
    _AutosuggestSendTelemetry

    # Show next-step suggestions after a new command completes
    $lastEntry = Get-History -Count 1
    if ($lastEntry -and $lastEntry.Id -ne $global:_AutosuggestLastNextStepId) {
        $global:_AutosuggestLastNextStepId = $lastEntry.Id
        $py = _AutosuggestGetPython
        if ($py) {
            $cmd = $lastEntry.CommandLine
            $nextSteps = & $py -m autosuggest.nextsteps_cli $cmd $PWD.Path 2>$null
            if ($nextSteps) {
                $items = if ($nextSteps -is [array]) { $nextSteps } else { @($nextSteps) }
                if ($items.Count -gt 0 -and $items[0].Length -gt 0) {
                    Write-Host ""
                    Write-Host "  Next steps:" -ForegroundColor Cyan
                    $i = 1
                    $global:_AutosuggestNextSteps = @()
                    foreach ($line in $items) {
                        $parts = $line -split "`t"
                        $suggestion = $parts[0]
                        $source = if ($parts.Count -gt 1) { $parts[1] } else { "" }
                        $global:_AutosuggestNextSteps += $suggestion
                        Write-Host "  [$i] " -NoNewline -ForegroundColor White
                        Write-Host ("{0,-40}" -f $suggestion) -NoNewline -ForegroundColor Yellow
                        if ($source) {
                            Write-Host " ($source)" -ForegroundColor DarkGray
                        } else {
                            Write-Host ""
                        }
                        $i++
                    }
                    Write-Host ""
                }
            }
        }
    }

    if ($global:_AutosuggestOriginalPrompt) {
        & ([scriptblock]::Create($global:_AutosuggestOriginalPrompt))
    } else {
        "PS $($PWD.Path)> "
    }
}
'''

TCSH_HOOK = r'''
# autosuggest-cli: telemetry + next-step hook for tcsh/csh
#
# tcsh has no programmable line editor, so this hook does NOT provide inline
# ghost text, an accept-suggestion key, or frecency Tab completion (use the
# zsh hook or the `suggest` REPL for those). In your native tcsh login shell
# it does give you:
#   * Telemetry recording ......... yes (records every command)
#   * Next-step suggestions ....... yes (printed after each command)
#   * Inline ghost text ........... no  (tcsh limitation)
#   * Frecency Tab completion ..... no  (tcsh limitation)
#
# Load it with tcsh-native BACKTICKS — csh/tcsh CANNOT parse $(...):
#     eval `suggest-hook tcsh`
# Persist it by adding that line to ~/.tcshrc (or ~/.cshrc.user on managed
# hosts). Silence next-step output with:  setenv AUTOSUGGEST_NO_NEXTSTEPS 1

# Put pip --user scripts on PATH so suggest-* resolve, then refresh the hash.
if ( -d ~/.local/bin ) then
    set path = ( $HOME/.local/bin $path )
    rehash
endif

# Resolve a python interpreter once per session.
if ( ! $?_AUTOSUGGEST_PY ) then
    if ( -X python3 ) then
        set _AUTOSUGGEST_PY = python3
    else if ( -X python ) then
        set _AUTOSUGGEST_PY = python
    else
        set _AUTOSUGGEST_PY = ""
    endif
endif

# Start the telemetry daemon if it is not already running (best-effort).
if ( "$_AUTOSUGGEST_PY" != "" ) then
    if ( $?XDG_RUNTIME_DIR ) then
        set _as_sock = "$XDG_RUNTIME_DIR/autosuggest.sock"
    else
        set _as_sock = "/tmp/autosuggest-`id -u`/autosuggest.sock"
    endif
    if ( ! -e "$_as_sock" ) then
        if ( -X suggest-daemon ) then
            ( suggest-daemon start >& /dev/null & )
        else
            ( $_AUTOSUGGEST_PY -m autosuggest.daemon start >& /dev/null & )
        endif
    endif
    unset _as_sock
endif

# precmd runs before every prompt: record the command just finished and show
# next-step suggestions. $status MUST be captured on the very first statement
# so it reflects the command that just ran.
alias precmd 'set _as_st = $status; set _as_cmd = "`history -h 1`"; if ( "$_AUTOSUGGEST_PY" != "" ) $_AUTOSUGGEST_PY -m autosuggest.tcsh_precmd "$_as_cmd:q" "$cwd" $_as_st'
'''

BASH_SOURCE_LINE = 'eval "$(suggest-hook bash)"'
ZSH_SOURCE_LINE = 'eval "$(suggest-hook zsh)"'
PS_SOURCE_LINE = 'Invoke-Expression ((& suggest-hook powershell) -join "`n")'
# tcsh/csh cannot parse $(...); it uses backticks for command substitution.
TCSH_SOURCE_LINE = 'if ( -X suggest-hook ) eval `suggest-hook tcsh`'


def _print_hook(shell: str) -> None:
    if shell == "bash":
        print(BASH_HOOK.strip())
    elif shell == "zsh":
        print(ZSH_HOOK.strip())
    elif shell == "powershell":
        print(POWERSHELL_HOOK.strip())
    elif shell in ("tcsh", "csh"):
        print(TCSH_HOOK.strip())
    else:
        print(f"[hook] unknown shell: {shell}")
        print("Usage: suggest-hook [bash|zsh|tcsh|powershell|install <shell>]")


def _install_hook(shell: str) -> None:
    if shell == "bash":
        bashrc = Path.home() / ".bashrc"
        source_line = f'\n# autosuggest-cli telemetry hook\n{BASH_SOURCE_LINE}\n'
        if bashrc.exists() and BASH_SOURCE_LINE in bashrc.read_text():
            print(f"[hook] already installed in {bashrc}")
            return
        with open(bashrc, "a", encoding="utf-8") as f:
            f.write(source_line)
        print(f"[hook] installed bash hook in {bashrc}")
        print(f"  Run: source ~/.bashrc")

    elif shell == "zsh":
        zshrc = Path(os.environ.get("ZDOTDIR", str(Path.home()))) / ".zshrc"
        source_line = f'\n# autosuggest-cli telemetry hook\n{ZSH_SOURCE_LINE}\n'
        if zshrc.exists() and ZSH_SOURCE_LINE in zshrc.read_text():
            print(f"[hook] already installed in {zshrc}")
            return
        with open(zshrc, "a", encoding="utf-8") as f:
            f.write(source_line)
        print(f"[hook] installed zsh hook in {zshrc}")
        print(f"  Run: source ~/.zshrc")

    elif shell in ("tcsh", "csh"):
        tcshrc = Path.home() / ".tcshrc"
        # tcsh needs ~/.local/bin on PATH (and a rehash) *before* it can find
        # suggest-hook, so the persisted block bootstraps PATH first, then evals
        # the hook with tcsh-native backticks.
        block = (
            "\n# autosuggest-cli telemetry hook\n"
            "if ( -d ~/.local/bin ) then\n"
            "    set path = ( $HOME/.local/bin $path )\n"
            "    rehash\n"
            "endif\n"
            f"{TCSH_SOURCE_LINE}\n"
        )
        if tcshrc.exists() and TCSH_SOURCE_LINE in tcshrc.read_text():
            print(f"[hook] already installed in {tcshrc}")
            return
        with open(tcshrc, "a", encoding="utf-8") as f:
            f.write(block)
        print(f"[hook] installed tcsh hook in {tcshrc}")
        print(f"  Run: source ~/.tcshrc")
        print("  Note: tcsh gets telemetry + next-steps only (no inline ghost text).")

    elif shell == "powershell":
        # PowerShell profile path
        profile = Path(os.environ.get(
            "USERPROFILE", str(Path.home())
        )) / "Documents/PowerShell/Microsoft.PowerShell_profile.ps1"

        # Fall back to WindowsPowerShell if Documents/PowerShell doesn't exist
        if not profile.parent.exists():
            profile = Path(os.environ.get(
                "USERPROFILE", str(Path.home())
            )) / "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1"

        profile.parent.mkdir(parents=True, exist_ok=True)
        source_line = f'\n# autosuggest-cli telemetry hook\n{PS_SOURCE_LINE}\n'

        if profile.exists() and PS_SOURCE_LINE in profile.read_text():
            print(f"[hook] already installed in {profile}")
            return

        with open(profile, "a", encoding="utf-8") as f:
            f.write(source_line)
        print(f"[hook] installed PowerShell hook in {profile}")
        print(f"  Restart PowerShell or run: . $PROFILE")
    else:
        print(f"[hook] unknown shell: {shell}")


def run_hook() -> None:
    """CLI entry point for suggest-hook."""
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print("Usage: suggest-hook <shell>          Print hook code to stdout")
        print("       suggest-hook install <shell>  Install hook into shell profile")
        print()
        print("Shells: bash, zsh, tcsh, powershell")
        print()
        print("Examples:")
        print('  eval "$(suggest-hook bash)"         # bash: add to ~/.bashrc')
        print('  eval "$(suggest-hook zsh)"          # zsh:  add to ~/.zshrc')
        print('  eval `suggest-hook tcsh`            # tcsh: add to ~/.tcshrc (BACKTICKS!)')
        print("  suggest-hook install powershell     # auto-install to $PROFILE")
        print()
        print("NOTE: csh/tcsh CANNOT parse $(...). In tcsh use backticks: `...`")
        print("      (running the bash line in tcsh gives 'Illegal variable name').")
        return

    if args[0] == "install" and len(args) >= 2:
        _install_hook(args[1])
    elif args[0] in ("bash", "zsh", "tcsh", "csh", "powershell"):
        _print_hook(args[0])
    else:
        print(f"[hook] unknown command: {' '.join(args)}")
        print("Usage: suggest-hook [bash|zsh|tcsh|powershell|install <shell>]")


if __name__ == "__main__":
    run_hook()
