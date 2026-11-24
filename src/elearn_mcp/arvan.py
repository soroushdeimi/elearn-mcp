"""Solve the ArvanCloud __arcsjs challenge page with a node subprocess."""

import importlib.resources
import json
import subprocess


class ChallengeError(Exception):
    pass


def is_challenge(html: str) -> bool:
    return "__arcsjs" in html and "hash_v1" in html


def solve(html: str, node: str = "node", timeout: float = 10.0) -> dict[str, str]:
    script = importlib.resources.files("elearn_mcp") / "arvan.js"
    # node's vm is not a security boundary; the permission model blocks fs/process access
    try:
        proc = subprocess.run(
            [node, "--permission", f"--allow-fs-read={script}", str(script)],
            input=html,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise ChallengeError(f"node not found: {node}") from e
    except subprocess.TimeoutExpired as e:
        raise ChallengeError(f"node timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise ChallengeError(f"arvan.js failed: {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ChallengeError(f"arvan.js output is not valid JSON: {proc.stdout!r}") from e
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key] for key in ("arcsjs", "arcsjsc")
    ):
        raise ChallengeError(f"arvan.js output is missing arcsjs/arcsjsc: {proc.stdout!r}")
    return {"__arcsjs": data["arcsjs"], "__arcsjsc": data["arcsjsc"]}
