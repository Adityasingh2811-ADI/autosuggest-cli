"""Measure hook overhead — times the socat round-trip to the daemon socket."""

import os
import socket
import sys
import time

from autosuggest.paths import socket_path


def run() -> None:
    sock_path = socket_path()
    iterations = 10

    if not os.path.exists(sock_path):
        print("  Daemon socket not found. Start the daemon first: suggest-daemon start")
        sys.exit(1)

    payload = b'{"command":"benchmark-ping","cwd":"/tmp","exit_status":0}'
    times = []

    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(sock_path)
            s.sendall(payload)
            s.close()
            elapsed_us = (time.perf_counter_ns() - t0) / 1000
            times.append(elapsed_us)
        except OSError as e:
            print(f"  Connection failed: {e}")
            sys.exit(1)

    avg = sum(times) / len(times)
    mn = min(times)
    mx = max(times)

    print(f"\n  suggest-benchmark — hook overhead measurement")
    print(f"  {'─' * 45}")
    print(f"  Iterations:  {iterations}")
    print(f"  Avg:         {avg:.0f} µs ({avg/1000:.2f} ms)")
    print(f"  Min:         {mn:.0f} µs ({mn/1000:.2f} ms)")
    print(f"  Max:         {mx:.0f} µs ({mx/1000:.2f} ms)")
    if avg < 5000:
        print(f"  Status:      \033[32mOK (<5ms)\033[0m")
    else:
        print(f"  Status:      \033[33mSLOW (>{avg/1000:.1f}ms)\033[0m")
    print()


if __name__ == "__main__":
    run()