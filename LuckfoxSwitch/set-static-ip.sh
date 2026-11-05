#!/bin/sh
# Configure eth0 and usb0 with static IPv4 addresses on the Luckfox and make
# them persistent across reboots (BusyBox init). Run ON the board as root:
#
#   ./set-static-ip.sh <ip> <netmask> <gateway>
#
# Example:
#   ./set-static-ip.sh 192.168.86.198 255.255.255.0 192.168.86.1
set -eu

IP="${1:?usage: $0 <ip> <netmask> <gateway>}"
NETMASK="${2:?missing netmask}"
GATEWAY="${3:?missing gateway}"
USB_IP="${CIVSWITCH_USB_IP:-192.168.77.1}"
USB_NETMASK="${CIVSWITCH_USB_NETMASK:-255.255.255.0}"
ETH_IF="${CIVSWITCH_ETH_IF:-eth0}"

# S99zz-ipstatic runs after the OEM RkLunch.sh DHCP client so the static
# address wins. Luckfox's stock image starts `udhcpc -i eth0` in the
# background at S21appinit; killing it here prevents a late DHCP lease from
# overwriting the static address after boot.
INIT=/etc/init.d/S99zz-ipstatic

cat > "$INIT" <<EOF
#!/bin/sh
# Static Ethernet and USB-network configuration (managed by CivSwitch).
case "\$1" in
    start)
        # Models such as the Pico, Mini, Zero, and WebBee may not expose
        # Ethernet. Do not make installation fail when the interface is absent.
        if [ -e "/sys/class/net/$ETH_IF" ]; then
            killall udhcpc 2>/dev/null || true
            ifconfig "$ETH_IF" $IP netmask $NETMASK up
            # A gateway is optional on a direct link; ignore failures.
            route add default gw $GATEWAY 2>/dev/null || true
        fi
        if [ -e /sys/class/net/usb0 ]; then
            ifconfig usb0 $USB_IP netmask $USB_NETMASK up
        fi
        ;;
    stop)
        ifconfig "$ETH_IF" down 2>/dev/null || true
        ;;
    restart)
        "\$0" stop
        "\$0" start
        ;;
    *)
        echo "Usage: \$0 {start|stop|restart}"
        exit 1
        ;;
esac
EOF

chmod +x "$INIT"

# Apply now without waiting for a reboot.
rm -f /etc/init.d/S40ipstatic
if [ -e "/sys/class/net/$ETH_IF" ]; then
    killall udhcpc 2>/dev/null || true
    ifconfig "$ETH_IF" "$IP" netmask "$NETMASK" up
    route add default gw "$GATEWAY" 2>/dev/null || true
fi
if [ -e /sys/class/net/usb0 ]; then
    ifconfig usb0 "$USB_IP" netmask "$USB_NETMASK" up
fi
sync

echo "Static network configured:"
if [ -e "/sys/class/net/$ETH_IF" ]; then
    ifconfig "$ETH_IF"
else
    echo "$ETH_IF not present; Ethernet configuration skipped."
fi
if [ -e /sys/class/net/usb0 ]; then
    ifconfig usb0
fi
