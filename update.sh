#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$project_dir"

usage() {
    echo "Usage: bash update.sh [update.zip]" >&2
}

if ! command -v unzip >/dev/null 2>&1; then
    echo "Error: unzip is not installed. Install it with: sudo apt install unzip" >&2
    exit 1
fi

if (( $# > 1 )); then
    usage
    exit 1
fi

if (( $# == 1 )); then
    archive="$1"
    if [[ "$archive" != /* ]]; then
        archive="$project_dir/$archive"
    fi
else
    shopt -s nullglob
    archives=("$project_dir"/*.zip)
    shopt -u nullglob

    if (( ${#archives[@]} == 0 )); then
        echo "Error: no .zip update was found in $project_dir" >&2
        exit 1
    fi

    if (( ${#archives[@]} > 1 )); then
        echo "Error: more than one .zip file was found:" >&2
        printf '  %s\n' "${archives[@]##*/}" >&2
        echo "Choose one explicitly, for example:" >&2
        echo "  bash update.sh ${archives[0]##*/}" >&2
        exit 1
    fi

    archive="${archives[0]}"
fi

if [[ ! -f "$archive" ]]; then
    echo "Error: update archive not found: $archive" >&2
    exit 1
fi

archive_dir="$(cd -- "$(dirname -- "$archive")" && pwd -P)"
archive_name="$(basename -- "$archive")"
archive="$archive_dir/$archive_name"

if [[ "$archive_dir" != "$project_dir" ]]; then
    echo "Error: the update ZIP must be in the project directory." >&2
    exit 1
fi

if [[ "$archive_name" != *.zip ]]; then
    echo "Error: the update file must end in .zip" >&2
    exit 1
fi

echo "Checking $archive_name..."
unzip -tq "$archive" >/dev/null

unzip -Z1 "$archive" | while IFS= read -r entry; do
    if [[ "$entry" == /* || "$entry" == ".." || "$entry" == ../* || "$entry" == */../* || "$entry" == */.. ]]; then
        echo "Error: unsafe path in update archive: $entry" >&2
        exit 1
    fi
done

echo "Installing update..."
unzip -oq "$archive" -d "$project_dir"

rm -- "$archive"
echo "Files updated. Removed $archive_name."

if command -v uv >/dev/null 2>&1; then
    echo "Installing any changed Python dependencies..."
    uv sync --extra dev
    test_command=(uv run python -m pytest)
    start_command=(uv run python run.py)
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Error: Python 3 is not installed." >&2
        exit 1
    fi

    if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
        echo "Creating Python virtual environment..."
        python3 -m venv "$project_dir/.venv"
    fi

    echo "Installing any changed Python dependencies..."
    "$project_dir/.venv/bin/python" -m pip install -e '.[dev]'
    test_command=("$project_dir/.venv/bin/python" -m pytest)
    start_command=("$project_dir/.venv/bin/python" run.py)
fi

echo "Running tests..."
"${test_command[@]}"

echo "All tests passed. Starting the site..."
exec "${start_command[@]}"
