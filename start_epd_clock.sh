#!/bin/sh
# Start epd_clock.py once the Pi has finished booting. Run from cron @reboot.

# Blocks until systemd reports startup complete ("running" or "degraded");
# capped so a stuck unit can't delay the clock forever.
timeout 180 systemctl is-system-running --wait >/dev/null 2>&1

# Then wait up to 2 minutes for the system clock to sync over the network.
i=0
while [ "$(timedatectl show -p NTPSynchronized --value)" != yes ] && [ "$i" -lt 60 ]; do
    sleep 2
    i=$((i + 1))
done

exec /usr/bin/python3 /home/admin/epd_clock.py --quiet >> /home/admin/epd_clock.log 2>&1
