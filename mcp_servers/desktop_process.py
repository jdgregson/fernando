import asyncio
import json
import subprocess
from pathlib import Path

import anyio


async def run_process(argv, *, input=None, timeout=30, check=False, keep_stdin=False):
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    reader = asyncio.gather(process.stdout.read(), process.stderr.read())
    try:
        async with asyncio.timeout(timeout):
            if input is not None:
                process.stdin.write(input.encode())
                await process.stdin.drain()
            if not keep_stdin:
                process.stdin.close()
            stdout, stderr = await asyncio.shield(reader)
            code = await process.wait()
            result = subprocess.CompletedProcess(argv, code, stdout.decode(), stderr.decode())
            if check:
                result.check_returncode()
            return result
    finally:
        with anyio.CancelScope(shield=True):
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), 3)
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def run_desktop(argv, *, input=None, env=None, timeout=30):
    request = json.dumps({"argv": argv, "input": input, "env": env or {}, "timeout": timeout})
    supervisor = Path(__file__).with_name("desktop_supervisor.py").read_text()
    return await run_process(
        ["docker", "exec", "-i", "--user", "1000:1000", "fernando-desktop",
         "/usr/bin/python3", "-u", "-c", supervisor],
        input=request + "\n", timeout=timeout + 5, keep_stdin=True,
    )
