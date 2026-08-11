#!/bin/sh
set -eu
mkdir -p /data/games
chown -R relaytest:relaytest /data
chmod 755 /data /data/games
if [ -f /config/relaytest.pub ]; then
  mkdir -p /home/relaytest/.ssh
  cp /config/relaytest.pub /home/relaytest/.ssh/authorized_keys
  chown -R relaytest:relaytest /home/relaytest/.ssh
  chmod 700 /home/relaytest/.ssh
  chmod 600 /home/relaytest/.ssh/authorized_keys
fi
exec /usr/sbin/sshd -D -e
