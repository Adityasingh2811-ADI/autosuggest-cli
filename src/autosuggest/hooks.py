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

# Resolve socket path (XDG on Linux, TCP fallback on Windows/WSL)
_autosuggest_sock="${XDG_RUNTIME_DIR:-/tmp/autosuggest-$(id -u)}/autosuggest.sock"

_autosuggest_ensure_daemon() {
    # Check if daemon is already listening (Unix socket or TCP fallback)
    if [[ -S "$_autosuggest_sock" ]]; then
        return
    fi
    (echo "" > /dev/tcp/127.0.0.1/19526) 2>/dev/null && return

    # Not running — start it detached
    if command -v autosuggest &>/dev/null; then
        autosuggest --daemon-bg 2>/dev/null
    elif command -v suggest-daemon &>/dev/null; then
        suggest-daemon start </dev/null &>/dev/null &
        disown
    elif command -v python3 &>/dev/null; then
        python3 -m autosuggest.daemon start </dev/null &>/dev/null &
        disown
    elif command -v python &>/dev/null; then
        python -m autosuggest.daemon start </dev/null &>/dev/null &
        disown
    fi
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

    # Escape double quotes and backslashes for JSON
    local escaped_cmd="${cmd//\\/\\\\}"
    escaped_cmd="${escaped_cmd//\"/\\\"}"
    local escaped_cwd="${PWD//\\/\\\\}"
    escaped_cwd="${escaped_cwd//\"/\\\"}"

    local payload
    payload=$(printf '{"command":"%s","cwd":"%s","exit_status":%d}' "$escaped_cmd" "$escaped_cwd" "$exit_status")

    # Fire-and-forget: prefer Unix socket, fall back to TCP
    if [[ -S "$_autosuggest_sock" ]]; then
        if command -v socat &>/dev/null; then
            echo "$payload" | socat - UNIX-CONNECT:"$_autosuggest_sock" 2>/dev/null &
        elif command -v python3 &>/dev/null; then
            python3 -c "
import socket,sys
try:
    s=socket.socket(socket.AF_UNIX);s.settimeout(0.1)
    s.connect('$_autosuggest_sock');s.sendall(sys.stdin.buffer.read());s.close()
except:pass
" <<< "$payload" 2>/dev/null &
        fi
    elif (echo "$payload" > /dev/tcp/127.0.0.1/19526) 2>/dev/null; then
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

# Start daemon on shell init (runs once per session, daemon persists across sessions)
_autosuggest_ensure_daemon

# Prepend to PROMPT_COMMAND without duplicating
if [[ "${PROMPT_COMMAND}" != *"_autosuggest_hook"* ]]; then
    PROMPT_COMMAND="_autosuggest_hook;${PROMPT_COMMAND:-:}"
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

BASH_SOURCE_LINE = 'eval "$(suggest-hook bash)"'
PS_SOURCE_LINE = 'Invoke-Expression ((& suggest-hook powershell) -join "`n")'


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
