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
    resume:*)
        line_number=${MODE#resume:}
        case "$line_number" in
            ''|*[!0-9]*|0) echo 'Invalid resume line' >&2; exit 1 ;;
        esac
        [ -c /dev/hidg0 ] && [ -c /dev/hidg1 ] || {
            echo 'HID is not connected; run civswitchctl prepare first.' >&2; exit 1;
        }
        args=(--resume "$line_number")
        ;;
    controller-test:*)
        button=${MODE#controller-test:}
        args=(--test-controller "$button")
        ;;
    controller-warmup-test:*)
        button=${MODE#controller-warmup-test:}
        args=(--test-controller-warmup "$button")
        ;;
    controller-preamble-test) args=(--test-controller-preamble) ;;
    keyboard-test) args=(--test-keyboard) ;;
    keyboard-alpha-test) args=(--test-keyboard-alpha) ;;
    keyboard-key:*)
        key_mode=${MODE#keyboard-key:}
        case "$key_mode" in
            slow|repeat|prime) ;;
            *) echo "CivSwitch: invalid keyboard key mode '$key_mode'" >&2; exit 1 ;;
        esac
        args=(--test-keyboard-key "$key_mode")
        ;;
    chat-keyboard-test) args=(--test-chat-keyboard) ;;
    raw-line:*)
        line_number=${MODE#raw-line:}
        case "$line_number" in
            ''|*[!0-9]*|0) echo "CivSwitch: invalid line '$line_number'" >&2; exit 1 ;;
        esac
        args=(--raw-line "$line_number")
        ;;
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
case "$MODE" in
    resume:*) echo 'CivSwitch: reusing registered HID connection' ;;
    *) /opt/civswitch/usb-gadget-hid.sh ;;
esac
python3 -u /opt/civswitch/civswitch.py "${args[@]}" &
CHILD_PID=$!
echo "$CHILD_PID" > "$CHILD_PIDFILE"
wait "$CHILD_PID"
