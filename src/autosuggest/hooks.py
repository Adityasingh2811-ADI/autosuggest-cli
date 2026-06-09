"""
Shell integration hooks — provides bash and PowerShell snippets that record
telemetry to the autosuggest daemon from the user's real shell.
"""

import os
import sys
from pathlib import Path

BASH_HOOK = r'''
# autosuggest-cli: telemetry hook for bash
# Records each command to the autosuggest daemon for frecency-based suggestions.
_autosuggest_last_hist=""

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

    # Escape double quotes and backslashes for JSON
    local escaped_cmd="${cmd//\\/\\\\}"
    escaped_cmd="${escaped_cmd//\"/\\\"}"
    local escaped_cwd="${PWD//\\/\\\\}"
    escaped_cwd="${escaped_cwd//\"/\\\"}"

    local payload
    payload=$(printf '{"command":"%s","cwd":"%s","exit_status":%d}' "$escaped_cmd" "$escaped_cwd" "$exit_status")

    # Fire-and-forget: try /dev/tcp first (bash builtin), fall back to python
    if (echo "$payload" > /dev/tcp/127.0.0.1/19526) 2>/dev/null; then
        :
    elif command -v python3 &>/dev/null; then
        python3 -c "
import socket,sys
try:
    s=socket.socket();s.settimeout(0.1);s.connect(('127.0.0.1',19526))
    s.sendall(sys.stdin.buffer.read());s.close()
except:pass
" <<< "$payload" 2>/dev/null &
    elif command -v python &>/dev/null; then
        python -c "
import socket,sys
try:
    s=socket.socket();s.settimeout(0.1);s.connect(('127.0.0.1',19526))
    s.sendall(sys.stdin.buffer.read());s.close()
except:pass
" <<< "$payload" 2>/dev/null &
    fi
}

# Prepend to PROMPT_COMMAND without duplicating
if [[ "${PROMPT_COMMAND}" != *"_autosuggest_hook"* ]]; then
    PROMPT_COMMAND="_autosuggest_hook;${PROMPT_COMMAND:-:}"
fi
'''

POWERSHELL_HOOK = r'''
# autosuggest-cli: telemetry hook for PowerShell
# Records each command to the autosuggest daemon for frecency-based suggestions.

$global:_AutosuggestLastHistId = -1

function global:_AutosuggestSendTelemetry {
    $lastEntry = Get-History -Count 1
    if (-not $lastEntry) { return }
    if ($lastEntry.Id -eq $global:_AutosuggestLastHistId) { return }
    $global:_AutosuggestLastHistId = $lastEntry.Id

    $cmd = $lastEntry.CommandLine
    $cwd = $PWD.Path
    $exitStatus = if ($lastEntry.ExecutionStatus -eq 'Completed') { 0 } else { 1 }

    # Escape for JSON
    $cmd = $cmd -replace '\\', '\\' -replace '"', '\"'
    $cwd = $cwd -replace '\\', '\\' -replace '"', '\"'

    $payload = "{`"command`":`"$cmd`",`"cwd`":`"$cwd`",`"exit_status`":$exitStatus}"

    # Fire-and-forget via .NET TCP
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

# Override prompt to inject telemetry
$global:_AutosuggestOriginalPrompt = if (Test-Path Function:\prompt) {
    Get-Content Function:\prompt
} else { $null }

function global:prompt {
    _AutosuggestSendTelemetry
    if ($global:_AutosuggestOriginalPrompt) {
        & ([scriptblock]::Create($global:_AutosuggestOriginalPrompt))
    } else {
        "PS $($PWD.Path)> "
    }
}
'''

BASH_SOURCE_LINE = 'eval "$(suggest-hook bash)"'
PS_SOURCE_LINE = '. (suggest-hook powershell | Out-String | Invoke-Expression)'


def _print_hook(shell: str) -> None:
    if shell == "bash":
        print(BASH_HOOK.strip())
    elif shell == "powershell":
        print(POWERSHELL_HOOK.strip())
    else:
        print(f"[hook] unknown shell: {shell}")
        print("Usage: suggest-hook [bash|powershell|install bash|install powershell]")


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
        print("Shells: bash, powershell")
        print()
        print("Examples:")
        print('  eval "$(suggest-hook bash)"         # Add to ~/.bashrc')
        print("  suggest-hook install powershell     # Auto-install to $PROFILE")
        return

    if args[0] == "install" and len(args) >= 2:
        _install_hook(args[1])
    elif args[0] in ("bash", "powershell"):
        _print_hook(args[0])
    else:
        print(f"[hook] unknown command: {' '.join(args)}")
        print("Usage: suggest-hook [bash|powershell|install bash|install powershell]")


if __name__ == "__main__":
    run_hook()
