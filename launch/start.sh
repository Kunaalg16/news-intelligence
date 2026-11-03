#!/bin/bash
# start.sh -- what opening the "News Intelligence" app runs (K1 integration brief §4.1,
# I3), and again when its Dock icon is clicked while it is open. Quitting the app runs
# stop.sh (Kunaal K-2, 3 Nov).
#
# Checks each part and starts ONLY what is not running, in order:
#   1. the embedding service  (.venv-embed/bin/python src/p3_embedsvc.py, port 8766),
#      waiting until /health answers;
#   2. the app                (.venv/bin/python src/p3_app.py, port 8765),
#      waiting until /api/status answers;
#   3. Safari on http://127.0.0.1:8765 -- an open tab on it is brought to the front,
#      never reloaded, so clicking again never loses the page you were on.
# Running it again never stops anything (idempotent). A part that fails to start gets
# one plain macOS notification naming it; the rest still starts (the app works without
# meaning search: the page then says "Word search only").
# Log: data/logs/launcher.log; each server's own output: data/logs/app.log, embedsvc.log.
#
# Usage: launch/start.sh           (the app runs exactly this)
#        launch/start.sh --no-open (start the parts, leave Safari alone; for checks)

set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"

# ---- knobs (named, with their reason) ----------------------------------------------
# Ports and commands are the servers' own defaults; the NI_* overrides exist only so
# the launcher can be checked on spare ports without touching the real servers.
APP_PORT="${NI_APP_PORT:-8765}"
EMBED_PORT="${NI_EMBED_PORT:-8766}"
APP_CMD="${NI_APP_CMD:-$ROOT/.venv/bin/python $ROOT/src/p3_app.py}"
EMBED_CMD="${NI_EMBED_CMD:-$ROOT/.venv-embed/bin/python $ROOT/src/p3_embedsvc.py}"
LOG_DIR="${NI_LOG_DIR:-$ROOT/data/logs}"
# How long to wait for each part to answer. GUESSED, not measured: the embedding model
# loads in tens of seconds on this Mac, the app in a few. Measured by: the "ready after
# Ns" lines this script writes to the log; lower them once a week of starts is logged.
EMBED_WAIT_S="${NI_EMBED_WAIT_S:-180}"
APP_WAIT_S="${NI_APP_WAIT_S:-60}"

APP_URL="http://127.0.0.1:$APP_PORT"
EMBED_HEALTH="http://127.0.0.1:$EMBED_PORT/health"
APP_HEALTH="$APP_URL/api/status"
OPEN_SAFARI=1
[ "${1:-}" = "--no-open" ] && OPEN_SAFARI=0

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/launcher.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') start: $*" >> "$LOG"; }
notify() {   # one plain notification; a refusal (no GUI session) is logged, not fatal
    log "notify: $1"
    /usr/bin/osascript -e "display notification \"$1\" with title \"News Intelligence\"" \
        >/dev/null 2>&1 || log "(notification could not be shown)"
}
answers() { /usr/bin/curl -sf -m 2 -o /dev/null "$1"; }

# ---- one start at a time: a double click must not start everything twice ---------------
LOCK="$LOG_DIR/.launcher.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    held="$(cat "$LOCK/pid" 2>/dev/null)"
    if [ -n "$held" ] && kill -0 "$held" 2>/dev/null; then
        log "another start (pid $held) is still running; this click does nothing"
        exit 0
    fi
    log "stale lock from pid ${held:-?} removed"
    rm -rf "$LOCK"
    mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# ---- start one part if it does not answer ----------------------------------------------
# $1 name · $2 health URL · $3 command · $4 its log file (its pid goes beside it,
# NAME.pid) · $5 seconds to wait
ensure() {
    local name="$1" url="$2" cmd="$3" out="$4" wait_s="$5" pid i
    local pidfile="${out%.log}.pid"
    if answers "$url"; then
        log "$name already running ($url answers); nothing started"
        return 0
    fi
    log "$name not answering; starting: $cmd"
    # nohup + every stream redirected: the server outlives this script and the icon,
    # and the icon's shell does not wait for the server's output to close.
    nohup $cmd >> "$out" 2>&1 < /dev/null &
    pid=$!
    disown "$pid" 2>/dev/null
    echo "$pid" > "$pidfile"     # how stop.sh knows this listener is ours
    for ((i = 1; i <= wait_s; i++)); do
        sleep 1
        if answers "$url"; then
            log "$name ready after ${i}s (pid $pid)"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            log "$name exited during start; see $out"
            rm -f "$pidfile"
            notify "$name did not start. See data/logs/$(basename "$out")."
            return 1
        fi
    done
    log "$name did not answer within ${wait_s}s (pid $pid left running); see $out"
    notify "$name did not start in time. See data/logs/$(basename "$out")."
    return 1
}

log "---- clicked"
if ! answers "$EMBED_HEALTH" || ! answers "$APP_HEALTH"; then
    notify "Starting…"
fi
ensure "Meaning search" "$EMBED_HEALTH" "$EMBED_CMD" "$LOG_DIR/embedsvc.log" "$EMBED_WAIT_S"
ensure "The app" "$APP_HEALTH" "$APP_CMD" "$LOG_DIR/app.log" "$APP_WAIT_S" || exit 1

# ---- Safari: front an open tab on the app, or open one -----------------------------
if [ "$OPEN_SAFARI" = 1 ]; then
    /usr/bin/osascript >> "$LOG" 2>&1 <<OSA || log "Safari could not be opened"
tell application "Safari"
    set found to false
    repeat with w in windows
        repeat with t in tabs of w
            try
                if (URL of t) starts with "$APP_URL" then
                    set current tab of w to t
                    set index of w to 1
                    set found to true
                    exit repeat
                end if
            end try
        end repeat
        if found then exit repeat
    end repeat
    if not found then open location "$APP_URL"
    activate
end tell
OSA
    log "Safari on $APP_URL"
fi
exit 0
