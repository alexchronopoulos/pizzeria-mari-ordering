#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$project_dir"

branch="${1:-master}"
if (( $# > 1 )); then
    echo "Usage: bash setup-git.sh [branch]" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "Error: Git is not installed." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 is not installed." >&2
    exit 1
fi

git check-ref-format --branch "$branch" >/dev/null

if [[ -d .git ]] && git rev-parse --verify HEAD >/dev/null 2>&1; then
    echo "Git is already initialized in $project_dir."
    python3 scripts/audit_public_repo.py
    exit 0
fi

if [[ ! -d .git ]]; then
    if git init -b "$branch" >/dev/null 2>&1; then
        :
    else
        git init >/dev/null
        git branch -M "$branch"
    fi
else
    git branch -M "$branch"
fi

git config core.hooksPath .githooks

echo "Auditing files intended for the public repository..."
python3 scripts/audit_public_repo.py

git add --all
python3 scripts/audit_public_repo.py --staged
git diff --cached --check

if [[ -z "$(git config user.name || true)" || -z "$(git config user.email || true)" ]]; then
    echo "Git was initialized and the safe source files were staged."
    echo "Set your Git name and email, then create the initial commit:"
    echo "  git config user.name \"Your Name\""
    echo "  git config user.email \"you@example.com\""
    echo "  git commit -m \"Initial ordering portal prototype\""
    exit 0
fi

git commit -m "Initial ordering portal prototype"

echo "Git setup is complete on branch $branch."
echo "After creating an empty GitHub repository, connect and push it with:"
echo "  git remote add origin git@github.com:YOUR-USER/pizzeria-mari-ordering.git"
echo "  git push -u origin $branch"
