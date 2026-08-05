#!/usr/bin/env python3
"""Reject files and high-confidence credential patterns unsafe for a public repo."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {
    ".venv",
    "venv",
    "instance",
    "__pycache__",
    ".pytest_cache",
    "pytest-of-root",
}
BLOCKED_NAMES = {
    ".env",
    ".envrc",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
}
BLOCKED_SUFFIXES = {
    ".db",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
}
TOKEN_PATTERNS = {
    "private key": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"
    ),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "Stripe live key": re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "credentialed URL": re.compile(
        r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE
    ),
    "assigned Square access token": re.compile(
        r"(?im)^\s*SQUARE_ACCESS_TOKEN\s*=\s*[\"']?(?!\s*(?:[\"']?$|<|your[-_]|replace|change-me))[^\s#\"']{8,}"
    ),
    "assigned AWS secret key": re.compile(
        r"(?im)^\s*AWS_SECRET_ACCESS_KEY\s*=\s*[\"']?(?!\s*(?:[\"']?$|<|your[-_]|replace|change-me))[^\s#\"']{8,}"
    ),
}


def git_paths(staged: bool) -> list[Path]:
    command = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
        if staged
        else ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
    return [ROOT / name for name in result.stdout.decode().split("\0") if name]


def blocked_path_reason(path: Path) -> str | None:
    relative = path.relative_to(ROOT)
    if any(part in BLOCKED_PARTS for part in relative.parts):
        return "local runtime or generated directory"
    if path.name in BLOCKED_NAMES:
        return "credential or local configuration filename"
    if path.name.startswith(".env.") and path.name != ".env.example":
        return "environment-specific configuration filename"
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        return "credential or local data file extension"
    return None


def content_findings(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except (FileNotFoundError, PermissionError):
        return []
    if b"\0" in data[:4096]:
        return []
    text = data.decode("utf-8", errors="ignore")
    return [name for name, pattern in TOKEN_PATTERNS.items() if pattern.search(text)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--staged",
        action="store_true",
        help="audit only files staged for the next commit",
    )
    args = parser.parse_args()

    findings: list[str] = []
    for path in git_paths(args.staged):
        reason = blocked_path_reason(path)
        if reason:
            findings.append(f"{path.relative_to(ROOT)}: {reason}")
            continue
        for token_type in content_findings(path):
            findings.append(f"{path.relative_to(ROOT)}: possible {token_type}")

    if findings:
        print("Public-repository audit failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        print("Remove the file or secret before committing.", file=sys.stderr)
        return 1

    scope = "staged files" if args.staged else "public file candidates"
    print(f"Public-repository audit passed for {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
