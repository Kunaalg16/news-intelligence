#!/bin/bash
# make_icons.sh -- builds the "News Intelligence" app (I3; Kunaal K-2, 3 Nov: ONE app).
# Run ONCE; run again only if this folder moves or the picture changes.
#
# The app is a stay-open AppleScript applet with three handlers and nothing else:
#   open                   -> launch/start.sh   (starts what is missing, opens Safari)
#   Dock click while open  -> launch/start.sh   (same; idempotent)
#   quit (Cmd-Q, Dock)     -> launch/stop.sh    (stops both servers), then quits
# Everything else lives in those scripts, so a change to them never needs a rebuild.
#
# Usage: launch/make_icons.sh            (puts it on the Desktop)
#        launch/make_icons.sh OUT_DIR    (anywhere else)

set -eu
cd "$(dirname "$0")/.."
ROOT="$PWD"
OUT="${1:-$HOME/Desktop}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ni_icons.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
APP="$OUT/News Intelligence.app"

# «event sysoexec» is `do shell script` written as its raw event code: it compiles even
# where the scripting dictionary is not loaded (the build sandbox), and osadecompile
# shows it back as `do shell script`. start.sh runs in the background so the app answers
# at once; stop.sh runs in the foreground so the app quits only after the servers have.
START="«event sysoexec» ((quoted form of \"$ROOT/launch/start.sh\") & \" > /dev/null 2>&1 &\")"
STOP="«event sysoexec» (quoted form of \"$ROOT/launch/stop.sh\")"
cat > "$WORK/main.applescript" <<OSA
on run
	$START
end run

on reopen
	$START
end reopen

on quit
	try
		$STOP
	end try
	continue quit
end quit
OSA

rm -rf "$APP"
/usr/bin/osacompile -s -o "$APP" "$WORK/main.applescript"        # -s: stay open

"$ROOT/.venv/bin/python" "$ROOT/launch/make_icon.py" icns \
    "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"   # the compiled default icon would win
# The icon changed the bundle, so its ad-hoc signature is renewed; an Apple Silicon Mac
# refuses to open an app whose seal is broken ("damaged").
/usr/bin/codesign --force --deep -s - "$APP"
touch "$APP"
echo "made $APP"
