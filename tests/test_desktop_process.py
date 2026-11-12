import asyncio
import json
from pathlib import Path
import sys
import time
import unittest

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_servers"))
from desktop_process import run_process


SUPERVISOR = Path(__file__).resolve().parents[1] / "mcp_servers" / "desktop_supervisor.py"


class DesktopProcessTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, argv, timeout=2, input=None):
        request = json.dumps({"argv": argv, "input": input, "env": {}, "timeout": timeout})
        return await run_process([sys.executable, str(SUPERVISOR)],
                                 input=request + "\n", timeout=timeout + 5, keep_stdin=True)

    async def test_input_and_exit_status(self):
        result = await self.execute([sys.executable, "-c", "import sys; print(sys.stdin.read()); sys.exit(17)"], input="hello")
        self.assertEqual(result.stdout, "hello\n")
        self.assertEqual(result.returncode, 17)

    async def test_timeout_does_not_block_another_request(self):
        pending = asyncio.create_task(self.execute([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.5))
        result = await asyncio.wait_for(self.execute([sys.executable, "-c", "print('ready')"]), 0.4)
        self.assertEqual(result.stdout, "ready\n")
        self.assertEqual((await pending).returncode, 124)

    async def test_cancellation_closes_supervisor_and_child(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "pid"
            script = "import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(marker)
            pending = asyncio.create_task(self.execute([sys.executable, "-c", script]))
            async with asyncio.timeout(2):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            pid = int(marker.read_text())
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertFalse(Path(f"/proc/{pid}").exists())

    async def test_anyio_cancellation(self):
        started = time.monotonic()
        with anyio.move_on_after(0.1) as scope:
            await self.execute([sys.executable, "-c", "import time; time.sleep(60)"])
        self.assertTrue(scope.cancel_called)
        self.assertLess(time.monotonic() - started, 2)

    async def test_inherited_output_pipe_cannot_hang(self):
        script = "import subprocess; subprocess.Popen(['sleep', '60'])"
        result = await asyncio.wait_for(self.execute([sys.executable, "-c", script]), 2)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
