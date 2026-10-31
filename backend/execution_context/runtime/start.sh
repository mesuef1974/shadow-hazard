#!/bin/sh
set -eu
node /opt/shadow/relay.js &
exec "$@"
