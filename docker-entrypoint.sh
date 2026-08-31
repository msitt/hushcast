#!/bin/sh

set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

groupmod -o -g "$PGID" hushcast
usermod -o -u "$PUID" hushcast

# Ownership is only set on /config and /data themselves, not recursively,
# to keep startup fast when /data holds a large audio library. Files the
# app creates from then on are owned correctly since it runs as PUID:PGID.
# If you change PUID/PGID after already having data, fix existing files
# yourself: chown -R <uid>:<gid> on your host config/data directories.
mkdir -p /config /data
chown hushcast:hushcast /config /data

exec gosu hushcast:hushcast "$@"
