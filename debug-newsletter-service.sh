#!/bin/sh
set -eu

SERVICE="${1:-newsletter-diaria.service}"

sudo systemctl daemon-reload
if ! sudo systemctl start "$SERVICE"; then
  echo "--- systemctl status ---"
  sudo systemctl status "$SERVICE" --no-pager || true
  echo "--- journalctl -xeu ---"
  sudo journalctl -xeu "$SERVICE" --no-pager || true
  exit 1
fi

sudo systemctl status "$SERVICE" --no-pager
sudo journalctl -xeu "$SERVICE" --no-pager
