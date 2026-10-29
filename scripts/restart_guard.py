import argparse
import http.client
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    connection = http.client.HTTPConnection("localhost", 5000, timeout=10)
    headers = {
        "X-API-Key": Path("/tmp/fernando-api-key").read_text().strip(),
        "Content-Type": "application/json",
    }
    connection.request("GET", "/api/agents/working", headers=headers)
    response = connection.getresponse()
    body = response.read()
    if response.status != 200:
        raise SystemExit(f"Restart blocked: working-agent check returned HTTP {response.status}. The running server must support the restart guard.")
    working_count = json.loads(body).get("working_count")
    if type(working_count) is not int or working_count < 0:
        raise SystemExit("Restart blocked: invalid working-agent count.")
    if working_count > 1 and not args.force:
        raise SystemExit(f"Restart blocked: {working_count} agents are currently working. Wait until at most one agent is working, or explicitly use force=true (scripts: --force).")
    connection.request(
        "POST",
        "/api/mutating",
        body=json.dumps({"force": args.force}),
        headers=headers,
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    if response.status != 200:
        raise SystemExit(payload.get("error", f"Restart check failed: HTTP {response.status}"))
    if payload.get("ok") is not True or type(payload.get("working_count")) is not int:
        raise SystemExit("Restart blocked: the running server does not support the working-agent guard. A manual service restart is required to activate it.")
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
