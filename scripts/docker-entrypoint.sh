#!/bin/sh
# Container start: create Fuse's tables on first boot, make a self-signed
# certificate if none is mounted, then run the server.
set -e
python -m scripts.init_db
if [ ! -f certs/server.crt ] || [ ! -f certs/server.key ]; then
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout certs/server.key -out certs/server.crt -subj "/CN=fuse" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
fi
mkdir -p data/desktop-releases
exec ./start.sh
