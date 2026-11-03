#!/bin/bash
# stop.sh -- what quitting the "News Intelligence" app runs (I3; Kunaal K-2: one app,
# quitting it stops both servers; no separate Stop icon).
#
# Stops the app (port 8765) and the embedding service (port 8766), cleanly: SIGTERM,
# then up to STOP_WAIT_S seconds to exit, and SIGKILL only if it is still there. A
# process is stopped only if it is listening on that port AND it is ours: its pid is the
# one start.sh recorded (data/logs/app.pid, embedsvc.pid), or its command line names that
# server's script (a server Kunaal started by hand in Terminal). Anything else holding
# the port is named in the log and left alone.
# Log: data/logs/launcher.log.

set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"

# ---- knobs ----------------------------------------------------------------------------
APP_PORT="${NI_APP_PORT:-8765}"
EMBED_PORT="${NI_EMBED_PORT:-8766}"
APP_MATCH="${NI_APP_MATCH:-src/p3_app.py}"          # what the app's command line holds
EMBED_MATCH="${NI_EMBED_MATCH:-src/p3_embedsvc.py}"
LOG_DIR="${NI_LOG_DIR:-$ROOT/data/logs}"
# uvicorn finishes the requests in flight on SIGTERM; a few seconds is plenty. GUESSED.
STOP_WAIT_S="${NI_STOP_WAIT_S:-10}"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/launcher.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') stop: $*" >> "$LOG"; }
notify() {
    log "notify: $1"
    /usr/bin/osascript -e "display notification \"$1\" with title \"News Intelligence\"" \
        >/dev/null 2>&1 || log "(notification could not be shown)"
}

# $1 name · $2 port · $3 command-line text it must hold · $4 start.sh's pid file.
# Echoes "stopped"/"none"/"kept".
stop_one() {
    local name="$1" port="$2" match="$3" pidfile="$4" pids pid cmd i result=none
    local recorded
    recorded="$(cat "$pidfile" 2>/dev/null)"
    pids="$(/usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)"
    for pid in $pids; do
        cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
        if [ "$pid" != "$recorded" ] && [[ "$cmd" != *"$match"* ]]; then
            log "$name: port $port is held by pid $pid (${cmd:-command line unreadable}), not ours; left alone"
            result=kept
            continue
        fi
        kill -TERM "$pid" 2>/dev/null
        for ((i = 1; i <= STOP_WAIT_S; i++)); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null
            log "$name (pid $pid) ignored SIGTERM for ${STOP_WAIT_S}s; killed"
        else
            log "$name (pid $pid) stopped"
        fi
        result=stopped
        [ "$pid" = "$recorded" ] && rm -f "$pidfile"
    done
    # Quit while a server is still loading: it is not listening yet, so the port shows
    # nothing. start.sh's recorded pid is then stopped too -- but only when its command
    # line names our script, so a stale pid file can never stop someone else's process.
    if [ -n "$recorded" ] && [ -f "$pidfile" ] && kill -0 "$recorded" 2>/dev/null; then
        cmd="$(ps -o command= -p "$recorded" 2>/dev/null)"
        if [[ "$cmd" == *"$match"* ]]; then
            kill -TERM "$recorded" 2>/dev/null
            log "$name (pid $recorded) was still starting; stopped"
            pids="$recorded"
            result=stopped
        fi
        rm -f "$pidfile"
    fi
    [ -z "$pids" ] && log "$name was not running"
    echo "$result"
}

log "---- clicked"
app="$(stop_one "The app" "$APP_PORT" "$APP_MATCH" "$LOG_DIR/app.pid")"
embed="$(stop_one "Meaning search" "$EMBED_PORT" "$EMBED_MATCH" "$LOG_DIR/embedsvc.pid")"
if [ "$app" = kept ] || [ "$embed" = kept ]; then
    notify "Something else is using a News Intelligence port. See data/logs/launcher.log."
else
    notify "Stopped."
fi
exit 0
