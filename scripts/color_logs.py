import re
import sys


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


def color_line(line):
    match = RECORD.fullmatch(line.rstrip("\n"))
    if not match:
        return line
    timestamp, logger, level, message = match.groups()
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
