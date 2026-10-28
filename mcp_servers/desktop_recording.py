import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(result.stderr or f"Command failed: {args[0]}")
    return result.stdout.strip()


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Expected a number between {low} and {high}")
    return value


def validate_actions(actions):
    if not isinstance(actions, list) or len(actions) > 200:
        raise ValueError("At most 200 actions are allowed")
    duration = 0
    for action in actions:
        kind = action["kind"]
        if kind in ("wait", "move"):
            duration += number(action.get("duration", 0), 0, 60)
        if kind == "move":
            for axis in ("x", "y"):
                number(action[axis], -32767, 32767)
            if not isinstance(action.get("relative", False), bool):
                raise ValueError("relative must be boolean")
        elif kind in ("key_down", "key_up"):
            if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", action["key"]):
                raise ValueError("Use one X keysym per key action, such as w or Shift_L")
        elif kind in ("button_down", "button_up"):
            if type(action["button"]) is not int or not 1 <= action["button"] <= 9:
                raise ValueError("button must be an integer from 1 to 9")
        elif kind != "wait":
            raise ValueError(f"Unknown action kind: {kind}")
    if duration > 60:
        raise ValueError("Input sequences are limited to 60 seconds")
    return duration


def move(action):
    duration = action.get("duration", 0)
    relative = action.get("relative", False)
    start = dict(line.split("=", 1) for line in run("xdotool", "getmouselocation", "--shell").splitlines())
    x0, y0 = (0, 0) if relative else (int(start["X"]), int(start["Y"]))
    x1, y1 = round(action["x"]), round(action["y"])
    steps = max(1, math.ceil(duration * 60))
    began = time.monotonic()
    previous = (0, 0)
    for index in range(1, steps + 1):
        delay = began + duration * index / steps - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        x = round(x0 + (x1 - x0) * index / steps)
        y = round(y0 + (y1 - y0) * index / steps)
        if relative:
            run("xdotool", "mousemove_relative", "--", str(x - previous[0]), str(y - previous[1]))
            previous = (x, y)
        else:
            run("xdotool", "mousemove", "--", str(x), str(y))


def sequence(actions):
    validate_actions(actions)
    held_keys = set()
    held_buttons = set()
    events = []
    began = time.monotonic()
    with open("/tmp/fernando-gameplay-input.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for action in actions:
                kind = action["kind"]
                events.append({"action": action, "started_seconds": time.monotonic() - began})
                if kind == "wait":
                    time.sleep(action.get("duration", 0))
                elif kind == "move":
                    move(action)
                elif kind == "key_down":
                    held_keys.add(action["key"])
                    run("xdotool", "keydown", "--", action["key"])
                elif kind == "key_up":
                    if action["key"] not in held_keys:
                        raise ValueError("Cannot release a key this sequence did not press")
                    run("xdotool", "keyup", "--", action["key"])
                    held_keys.remove(action["key"])
                elif kind == "button_down":
                    held_buttons.add(action["button"])
                    run("xdotool", "mousedown", str(action["button"]))
                elif kind == "button_up":
                    if action["button"] not in held_buttons:
                        raise ValueError("Cannot release a button this sequence did not press")
                    run("xdotool", "mouseup", str(action["button"]))
                    held_buttons.remove(action["button"])
        finally:
            cleanup = []
            for key in held_keys:
                cleanup.extend(["keyup", "--", key])
            for button in held_buttons:
                cleanup.extend(["mouseup", str(button)])
            if cleanup:
                run("xdotool", *cleanup)
    return {"events": events, "elapsed_seconds": time.monotonic() - began,
            "input_released": True, "timing": "Best-effort desktop timing, not a deterministic game simulation."}


def recording_path(recording_id):
    if not isinstance(recording_id, str) or not re.fullmatch(r"[0-9a-f]{32}", recording_id):
        raise ValueError("Invalid recording ID")
    return Path("/home/kasm-user") / f"gameplay_{recording_id}.mp4"


def publish(path):
    if os.environ["FERNANDO_HOST_SYSTEM"] == "Linux":
        uid = int(os.environ["FERNANDO_HOST_UID"])
        if uid < 0:
            raise ValueError("Invalid host UID")
        run("setfacl", "-m", f"u:{uid}:r", "--", str(path))


def record(request):
    duration = number(request["duration"], 1, 60)
    fps = number(request.get("fps", 30), 5, 60)
    actions = request.get("actions", [])
    if validate_actions(actions) > duration:
        raise ValueError("Input sequence cannot be longer than the recording")
    path = recording_path(request["recording_id"])
    geometry = re.search(r"dimensions:\s+(\d+x\d+)", run("xdpyinfo")).group(1)
    log = path.with_suffix(".log")
    ready = threading.Event()
    first_frame = []
    with log.open("x") as errors:
        process = subprocess.Popen([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
            "-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
            "-video_size", geometry, "-probesize", "32", "-analyzeduration", "0", "-i", ":1", "-t", str(duration),
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
            "-preset", "ultrafast", "-tune", "zerolatency", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-stats_period", "0.05", "-progress", "pipe:1", str(path),
        ], stdout=subprocess.PIPE, stderr=errors, text=True)
        def progress():
            for line in process.stdout:
                if line.startswith("frame=") and int(line.split("=", 1)[1]) > 0 and not first_frame:
                    first_frame.append(time.monotonic())
                    ready.set()
        reader = threading.Thread(target=progress, daemon=True)
        reader.start()
        try:
            if not ready.wait(8):
                raise RuntimeError(f"Recording did not produce a frame: {log.read_text()}")
            offset = time.monotonic() - first_frame[0]
            inputs = sequence(actions)
            code = process.wait(timeout=duration + 10)
            if code:
                raise RuntimeError(f"Recording failed: {log.read_text()}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader.join(timeout=2)
            process.stdout.close()
    actual = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)))
    publish(path)
    return {"recording_id": request["recording_id"], "duration": actual, "fps": fps,
            "geometry": geometry, "inputs": inputs, "input_start_after_first_frame_report_seconds": offset,
            "audio": False, "cursor": True}


def frames(request):
    path = recording_path(request["recording_id"])
    duration = float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)))
    timestamps = request["timestamps"]
    if not isinstance(timestamps, list) or not 1 <= len(timestamps) <= 12:
        raise ValueError("Request between 1 and 12 frames")
    for timestamp in timestamps:
        number(timestamp, 0, duration)
        if timestamp >= duration:
            raise ValueError("Frame timestamp must precede the end of the recording")
    results = []
    for index, timestamp in enumerate(timestamps):
        output = path.with_name(f"{path.stem}_{time.time_ns()}_{index}.png")
        run("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-ss", str(timestamp),
            "-i", str(path), "-frames:v", "1", "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease", str(output))
        if not output.is_file():
            raise RuntimeError(f"No frame decoded at {timestamp}")
        publish(output)
        results.append({"requested_seconds": timestamp, "file": output.name})
    return {"recording_id": request["recording_id"], "duration": duration, "frames": results}


def interrupted(signum, frame):
    raise InterruptedError(f"Desktop recording operation interrupted by signal {signum}")


if __name__ == "__main__":
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    request = json.loads(sys.stdin.readline())
    def disconnected():
        sys.stdin.read()
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=disconnected, daemon=True).start()
    operation = request.pop("operation")
    if operation == "sequence":
        result = sequence(request["actions"])
    elif operation == "record":
        result = record(request)
    elif operation == "frames":
        result = frames(request)
    else:
        raise ValueError("Unknown recording operation")
    print(json.dumps(result), flush=True)
    os._exit(0)
