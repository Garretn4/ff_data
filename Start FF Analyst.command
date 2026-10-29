#!/bin/bash
# Double click this file to start FF Analyst. On the first run it installs uv, which sets up everything else.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "First time setup: installing uv (a free helper that installs the rest)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "Could not install uv. Check your internet connection and try again."; read -r -p "Press Enter to close."; exit 1; }
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "Starting FF Analyst. The first run takes a few minutes. Close this window to quit."
uv run python -m app run
status=$?
if [ $status -ne 0 ]; then
  echo "FF Analyst stopped with an error (code $status)."
  read -r -p "Press Enter to close."
fi
