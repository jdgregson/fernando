import re
import sys
from datetime import datetime, timedelta


RECORD = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+) (\S+) "
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$"
)
COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}


def parse_line(line):
    match = RECORD.fullmatch(line.rstrip("\n"))
    if not match:
        return {"text": line.rstrip("\n")}
    timestamp, logger, level, message = match.groups()
    return {"text": line.rstrip("\n"), "timestamp": timestamp, "logger": logger, "level": level, "message": message}


def tail_lines(path, count):
    with open(path, "rb") as stream:
        stream.seek(0, 2)
        position = stream.tell()
        chunks = []
        newlines = 0
        while position and newlines <= count:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            chunk = stream.read(size)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
    return b"".join(reversed(chunks)).decode(errors="replace").splitlines()[-count:]


def recent_errors(path, now=None):
    now = now if now is not None else datetime.now()
    cutoff = now - timedelta(hours=1)
    errors = []
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            record = parse_line(line)
            if record.get("level") not in {"ERROR", "CRITICAL"}:
                continue
            timestamp = datetime.strptime(record["timestamp"], "%Y-%m-%d %H:%M:%S,%f")
            if cutoff < timestamp <= now:
                errors.append(record["text"])
    return errors


def color_line(line):
    record = parse_line(line)
    level = record.get("level")
    if not level:
        return line
    timestamp, logger, message = record["timestamp"], record["logger"], record["message"]
    color = COLORS[level]
    message_color = color if level != "INFO" else "\033[0m"
    ending = "\n" if line.endswith("\n") else ""
    return (
        f"\033[90m{timestamp}\033[0m \033[36m{logger}\033[0m "
        f"{color}{level}\033[0m {message_color}{message}\033[0m{ending}"
    )


def main():
    interactive = sys.stdout.isatty()
    for line in sys.stdin:
        sys.stdout.write(color_line(line) if interactive else line)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
