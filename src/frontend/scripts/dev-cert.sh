#!/usr/bin/env bash
# Generate a self-signed dev certificate so the vite dev servers can serve
# HTTPS. getUserMedia (microphone) only exists in secure contexts, and over
# plain HTTP only localhost counts as trustworthy — LAN IP access fails with
# "Microphone access denied". With HTTPS, testers click through the browser
# cert warning once per origin, then the mic works.
#
# Usage: scripts/dev-cert.sh [LAN_IP]   (defaults to this machine's first IP)
set -euo pipefail
cd "$(dirname "$0")/.."

IP="${1:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
SAN="DNS:localhost,IP:127.0.0.1"
[ -n "${IP:-}" ] && SAN="${SAN},IP:${IP}"

mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout certs/dev.key -out certs/dev.crt \
  -subj "/CN=localhost" \
  -addext "subjectAltName=${SAN}"

echo "Generated certs/dev.{key,crt} (SAN: ${SAN})"
echo "Enable with VITE_DEV_HTTPS=true in .env.development.local (client reads src/frontend/, platform reads platform/)."
