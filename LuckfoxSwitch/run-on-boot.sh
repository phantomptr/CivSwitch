#!/bin/bash
set -euo pipefail
# /var/log is volatile on the stock Buildroot image. Keep the most recent
# two boots on NAND, including a failed run after the board is unplugged.
LOG_DIR=${CIVSWITCH_LOG_DIR:-/opt/civswitch/logs}
mkdir -p "$LOG_DIR"
if [ -f "$LOG_DIR/current.log" ]; then
    mv -f "$LOG_DIR/current.log" "$LOG_DIR/previous.log"
fi
exec >> "$LOG_DIR/current.log" 2>&1

MODE="${1:-off}"
CHILD_PID=
CHILD_PIDFILE=/run/civswitch-child.pid

cleanup() {
    status=$?
    trap - EXIT INT TERM
    echo "CivSwitch: job cleanup started"
    if [ -n "${CHILD_PID:-}" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        echo "CivSwitch: stopping HID process PID $CHILD_PID"
        kill -TERM "$CHILD_PID" 2>/dev/null || true
        wait "$CHILD_PID" 2>/dev/null || true
    fi
    rm -f "$CHILD_PIDFILE"
    # The job runner is also used for manual starts. Never leave the Switch
    # endpoints bound, or USB ADB/RNDIS unavailable, after Python exits.
    echo "CivSwitch: restoring stock USB"
    if /opt/civswitch/restore-default-gadget.sh --from-job; then
        echo "CivSwitch: job cleanup complete"
    else
        echo "CivSwitch: job cleanup restore failed" >&2
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

echo "CivSwitch: starting $(date), mode=$MODE"
case "$MODE" in
    payload) args=(/opt/civswitch/payload.txt) ;;
    controller-test) args=(--test-controller) ;;
    line:*)
        line_number=${MODE#line:}
        case "$line_number" in
            ''|*[!0-9]*|0) echo "CivSwitch: invalid line '$line_number'" >&2; exit 1 ;;
        esac
        args=(--line "$line_number")
        ;;
    *)
        echo "CivSwitch: invalid mode '$MODE'" >&2
        exit 1
        ;;
esac

/opt/civswitch/setup-swap.sh || true
/opt/civswitch/usb-gadget-hid.sh
python3 -u /opt/civswitch/civswitch.py "${args[@]}" &
CHILD_PID=$!
echo "$CHILD_PID" > "$CHILD_PIDFILE"
wait "$CHILD_PID"
