#!/usr/bin/env python3
"""Read-only Claude/CC Switch configuration and endpoint health check."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


def load_settings(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def configuration(settings_path: Path) -> Dict[str, Any]:
    settings = load_settings(settings_path)
    configured = settings.get("env", {})
    env = configured if isinstance(configured, dict) else {}
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or str(env.get("ANTHROPIC_BASE_URL", ""))
    model = os.environ.get("ANTHROPIC_MODEL") or str(env.get("ANTHROPIC_MODEL", ""))
    parsed = urllib.parse.urlsplit(base_url)
    return {
        "claude_cli": shutil.which("claude"),
        "settings_path": str(settings_path),
        "settings_found": settings_path.is_file(),
        "base_url_configured": bool(base_url),
        "base_url_origin": (
            f"{parsed.scheme}://{parsed.hostname}"
            + (f":{parsed.port}" if parsed.port else "")
            if parsed.scheme and parsed.hostname else None
        ),
        "model": model or None,
        "auth_configured": bool(
            os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_API_KEY")
            or env.get("ANTHROPIC_AUTH_TOKEN")
            or env.get("ANTHROPIC_API_KEY")
        ),
    }


def probe(origin: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(origin + "/", method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": "reachable", "http_status": response.status, "error": None}
    except urllib.error.HTTPError as exc:
        return {"status": "reachable", "http_status": exc.code, "error": None}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            category = "dns"
        elif isinstance(reason, (socket.timeout, TimeoutError)):
            category = "timeout"
        elif isinstance(reason, ssl.SSLError):
            category = "tls"
        else:
            category = "connection"
        return {"status": "unreachable", "http_status": None, "error": category}
    except (socket.timeout, TimeoutError):
        return {"status": "unreachable", "http_status": None, "error": "timeout"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
    )
    parser.add_argument("--probe", action="store_true", help="Probe configured API origin without credentials.")
    parser.add_argument(
        "--require-probe",
        action="store_true",
        help="Fail when the optional endpoint probe fails; default is advisory.",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = configuration(args.settings.expanduser())
    if args.probe:
        origin = result.get("base_url_origin")
        result["probe"] = probe(origin, args.timeout) if origin else {
            "status": "skipped", "http_status": None, "error": "base-url-missing"
        }
    result["healthy"] = bool(result["claude_cli"] and result["base_url_configured"])
    result["probe_required"] = args.require_probe
    if args.require_probe:
        if not args.probe:
            result["probe"] = probe(result["base_url_origin"], args.timeout) if result.get("base_url_origin") else {
                "status": "skipped", "http_status": None, "error": "base-url-missing"
            }
        result["healthy"] = result["healthy"] and result["probe"]["status"] == "reachable"
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("Claude CLI: {}".format(result["claude_cli"] or "missing"))
        print("API origin: {}".format(result["base_url_origin"] or "not configured"))
        print("Model: {}".format(result["model"] or "not configured"))
        print("Auth configured: {}".format("yes" if result["auth_configured"] else "no"))
        if args.probe:
            print("Endpoint probe ({}): {} ({})".format(
                "required" if args.require_probe else "advisory",
                result["probe"]["status"],
                result["probe"]["error"] or result["probe"]["http_status"],
            ))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
