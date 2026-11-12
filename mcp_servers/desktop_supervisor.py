import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time


def main():
    request = json.loads(sys.stdin.buffer.readline())
    timeout = float(request["timeout"])
    if not 0 < timeout <= 120:
        raise ValueError("Desktop command timeout must be between 0 and 120 seconds")
    with tempfile.TemporaryFile() as source:
        source.write((request["input"] or "").encode())
        source.seek(0)
        process = subprocess.Popen(
            request["argv"], stdin=source,
            env={**os.environ, **request["env"]}, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        code = None
        try:
            while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print(f"Desktop command exceeded {timeout:g}s timeout", file=sys.stderr)
                    code = 124
                    break
                readable, _, _ = select.select([sys.stdin.fileno()], [], [], min(0.1, remaining))
                if readable and not os.read(sys.stdin.fileno(), 4096):
                    code = 125
                    break
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        return process.returncode if code is None else code


if __name__ == "__main__":
    sys.exit(main())
