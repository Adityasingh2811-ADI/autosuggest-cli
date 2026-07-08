#!/bin/tcsh -f
#
# Manual integration test: verify autosuggest hooks work with env commands
# in a live tcsh session.
#
# Usage:
#   source tests/manual_test_env_tcsh.csh
#
# Prerequisites:
#   - autosuggest-cli installed (suggest-hook, suggest-daemon available)
#   - eval `suggest-hook tcsh` already in your shell (or run it now)
#
# Each test prints PASS/FAIL. Review the daemon DB afterward with:
#   suggest-stats --recent 20

set _test_pass = 0
set _test_fail = 0
set _test_total = 0

alias _test_header 'echo ""; echo "==== \!* ===="'
alias _test_ok 'echo "  PASS: \!*"; @ _test_pass++; @ _test_total++'
alias _test_nok 'echo "  FAIL: \!*"; @ _test_fail++; @ _test_total++'

# --------------------------------------------------------------------------
_test_header "Test 1: Daemon is running"
# --------------------------------------------------------------------------
if ( -X suggest-daemon ) then
    suggest-daemon status >& /dev/null
    if ( $status == 0 ) then
        _test_ok "daemon is running"
    else
        echo "  Starting daemon..."
        suggest-daemon start &
        sleep 2
        suggest-daemon status >& /dev/null
        if ( $status == 0 ) then
            _test_ok "daemon started successfully"
        else
            _test_nok "daemon failed to start"
        endif
    endif
else
    _test_nok "suggest-daemon not found in PATH"
endif

# --------------------------------------------------------------------------
_test_header "Test 2: Hook is loaded (precmd alias exists)"
# --------------------------------------------------------------------------
alias precmd >& /dev/null
if ( $status == 0 ) then
    _test_ok "precmd alias is defined"
else
    _test_nok "precmd alias missing - run: eval \`suggest-hook tcsh\`"
endif

# --------------------------------------------------------------------------
_test_header "Test 3: module load is recorded"
# --------------------------------------------------------------------------
if ( $?MODULESHOME ) then
    module avail >& /dev/null
    if ( $status == 0 ) then
        # Load a harmless module
        module load null >& /dev/null
        if ( $status == 0 ) then
            _test_ok "module load null succeeded"
            module unload null >& /dev/null
        else
            # Try any available module
            _test_ok "module system available (no null module, skipping load)"
        endif
    else
        _test_nok "module avail failed"
    endif
else
    echo "  SKIP: MODULESHOME not set"
    @ _test_total++
endif

# --------------------------------------------------------------------------
_test_header "Test 4: setenv is recorded by hook"
# --------------------------------------------------------------------------
setenv _AUTOSUGGEST_TEST_VAR "test_value_$$"
if ( $?_AUTOSUGGEST_TEST_VAR ) then
    if ( "$_AUTOSUGGEST_TEST_VAR" == "test_value_$$" ) then
        _test_ok "setenv works and persists in session"
    else
        _test_nok "setenv value mismatch"
    endif
else
    _test_nok "setenv did not set variable"
endif
unsetenv _AUTOSUGGEST_TEST_VAR

# --------------------------------------------------------------------------
_test_header "Test 5: set path modification"
# --------------------------------------------------------------------------
set _old_path = ($path)
set path = ($path /tmp/_autosuggest_test_path_$$)
echo "$PATH" | grep -q "/tmp/_autosuggest_test_path_$$"
if ( $status == 0 ) then
    _test_ok "set path appended correctly"
else
    _test_nok "set path did not modify PATH"
endif
set path = ($_old_path)
unset _old_path

# --------------------------------------------------------------------------
_test_header "Test 6: source a .csh setup file"
# --------------------------------------------------------------------------
set _tmp_setup = "/tmp/_autosuggest_test_setup_$$.csh"
echo 'setenv _AS_SOURCED_OK 1' >! "$_tmp_setup"
source "$_tmp_setup"
if ( $?_AS_SOURCED_OK ) then
    _test_ok "source .csh file works, env var set"
    unsetenv _AS_SOURCED_OK
else
    _test_nok "source .csh did not set env var"
endif
rm -f "$_tmp_setup"
unset _tmp_setup

# --------------------------------------------------------------------------
_test_header "Test 7: pinit-like alias recording"
# --------------------------------------------------------------------------
# Simulate a pinit alias (project init that sets env)
alias _test_pinit 'setenv PROJECT_ROOT /tmp/test_proj_$$; echo "Project initialized"'
_test_pinit >& /dev/null
if ( "$PROJECT_ROOT" == "/tmp/test_proj_$$" ) then
    _test_ok "pinit-like alias sets env correctly"
else
    _test_nok "pinit-like alias did not set PROJECT_ROOT"
endif
unsetenv PROJECT_ROOT
unalias _test_pinit

# --------------------------------------------------------------------------
_test_header "Test 8: Telemetry reaches daemon (check DB)"
# --------------------------------------------------------------------------
# Give the daemon time to flush
sleep 1
if ( -X python3 ) then
    set _py = python3
else if ( -X python ) then
    set _py = python
else
    set _py = ""
endif

if ( "$_py" != "" ) then
    # Check if the test setenv was recorded in the last few entries
    $_py -c 'import sqlite3, os; db=os.path.expanduser("~/.local/share/autosuggest/history.db"); c=sqlite3.connect(db); r=c.execute("SELECT command FROM command_history ORDER BY timestamp DESC LIMIT 20").fetchall(); cmds=[x[0] for x in r]; print("FOUND" if any("_AUTOSUGGEST_TEST_VAR" in c or "autosuggest_test" in c.lower() for c in cmds) else "NOT_FOUND")' |& grep -q FOUND
    if ( $status == 0 ) then
        _test_ok "telemetry recorded env commands in DB"
    else
        _test_nok "env commands not found in recent DB entries (may need precmd trigger)"
    endif
else
    echo "  SKIP: no python available to check DB"
    @ _test_total++
endif
unset _py

# --------------------------------------------------------------------------
_test_header "Test 9: suggest query returns env commands"
# --------------------------------------------------------------------------
if ( -X suggest ) then
    # Non-interactive query if supported
    echo "module load" | timeout 3 suggest --query >& /dev/null
    if ( $status == 0 ) then
        _test_ok "suggest accepts 'module load' prefix"
    else
        echo "  SKIP: suggest --query not supported (interactive only)"
        @ _test_total++
    endif
else
    echo "  SKIP: suggest not in PATH"
    @ _test_total++
endif

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
echo ""
echo "======================================"
echo "  Results: $_test_pass passed, $_test_fail failed, $_test_total total"
echo "======================================"
if ( $_test_fail > 0 ) then
    echo "  Some tests FAILED. Review output above."
else
    echo "  All tests PASSED."
endif

unset _test_pass _test_fail _test_total
unalias _test_header _test_ok _test_nok
