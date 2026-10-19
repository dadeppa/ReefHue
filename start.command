#!/bin/bash
# ReefHue launcher for macOS (Apple Silicon).
# First run: creates .venv and installs requirements. Later runs: just start
# (re-installs only when requirements.txt changed).

cd "$(dirname "$0")" || exit 1
APP_DIR="$(pwd)"
VENV="$APP_DIR/.venv"
PY_URL="https://www.python.org/downloads/"

# Finder-launched shells may miss Homebrew / python.org paths.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"

pause_and_exit() {
    echo
    read -r -p "Press Enter to close this window..." _
    exit "${1:-1}"
}

# The app is unsigned: strip the download quarantine from the app folder (best effort).
xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true

# probe: 0 = usable, 2 = too old, 3 = Intel Python on Apple Silicon (reefhue/bootstrap.py)
WRONG_ARCH_SEEN=0
find_python() {
    local cand path code
    for cand in python3.12 python3.11 python3.13 python3; do
        path="$(command -v "$cand" 2>/dev/null)" || continue
        # Skip Apple's /usr/bin/python3 stub (Python 3.9, may pop up a CLT install dialog).
        [ "$path" = "/usr/bin/python3" ] && continue
        "$path" -m reefhue.bootstrap probe 2>/dev/null
        code=$?
        if [ $code -eq 0 ]; then
            echo "$path"
            return 0
        fi
        [ $code -eq 3 ] && WRONG_ARCH_SEEN=1
    done
    return 1
}

if [ -x "$VENV/bin/python" ]; then
    "$VENV/bin/python" -m reefhue.bootstrap probe
    VENV_CODE=$?
else
    VENV_CODE=1
fi

if [ $VENV_CODE -ne 0 ]; then
    PYTHON="$(find_python)"
    if [ -z "$PYTHON" ]; then
        if [ $WRONG_ARCH_SEEN -eq 1 ]; then
            echo "Only an Intel (x86_64) Python was found. On an Apple Silicon Mac ReefHue needs"
            echo "the native Apple Silicon Python 3.12 from python.org or Homebrew (brew install python@3.12)."
        else
            echo "ReefHue needs Python 3.11 or newer (3.12 recommended)."
        fi
        echo "Download: $PY_URL"
        open "$PY_URL" 2>/dev/null
        pause_and_exit 1
    fi
    if [ $VENV_CODE -eq 3 ]; then
        echo "The existing environment was created with an Intel Python – rebuilding it natively."
    fi
    echo "Setting up ReefHue with $PYTHON ..."
    rm -rf "$VENV"
    if ! "$PYTHON" -m venv "$VENV"; then
        echo "Could not create the Python environment in $VENV."
        pause_and_exit 1
    fi
fi

"$VENV/bin/python" -m reefhue.bootstrap check
CHECK=$?
if [ $CHECK -eq 1 ]; then
    echo "Installing / updating ReefHue dependencies (this can take a few minutes) ..."
    if ! "$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip \
        || ! "$VENV/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"; then
        echo
        echo "Installing the dependencies failed. Check your internet connection."
        echo "If you use a very new Python version, install Python 3.12 from $PY_URL,"
        echo "delete the .venv folder inside ReefHue and start again."
        pause_and_exit 1
    fi
    "$VENV/bin/python" -m reefhue.bootstrap stamp
elif [ $CHECK -ne 0 ]; then
    pause_and_exit $CHECK
fi

"$VENV/bin/python" -m reefhue "$@"
STATUS=$?
if [ $STATUS -ne 0 ]; then
    echo "ReefHue exited with an error (code $STATUS). See the log folder (Help > Open log folder)."
    pause_and_exit $STATUS
fi
exit 0
