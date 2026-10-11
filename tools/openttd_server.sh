#!/bin/sh
# Start (or restart) the headless OpenTTD server the CEO plays on.
#   tools/openttd_server.sh [seed]     -> admin port 3977 (password "mayor"), screenshots in $RUN/screenshot
ROOT=${ROOT:-/home/user}
RUN=${RUN:-$ROOT/ottd-run}
SEED=${1:-21}
HERE=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$RUN"
[ -f "$RUN/openttd.cfg" ] || cp "$HERE/openttd_scripts/openttd.cfg" "$RUN/openttd.cfg"
pkill -f "^./openttd -D" 2>/dev/null; sleep 1
cd "$ROOT/openttd-src/build" && LD_LIBRARY_PATH="$ROOT/local/lib" nohup setsid ./openttd -D 127.0.0.1:3979 \
  -b 32bpp-optimized -c "$RUN/openttd.cfg" -G "$SEED" > "$RUN/server.log" 2>&1 < /dev/null &
sleep 8
grep -q "starting game" "$RUN/server.log" && echo "OpenTTD server up (seed $SEED)" || { echo "server failed:"; tail "$RUN/server.log"; exit 1; }
