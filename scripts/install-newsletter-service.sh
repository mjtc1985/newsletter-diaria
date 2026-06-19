#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

sudo rm -rf /etc/systemd/system/newsletter-diaria.service.d
sudo cp "$ROOT_DIR/systemd/newsletter-diaria.service" /etc/systemd/system/
sudo cp "$ROOT_DIR/systemd/newsletter-diaria.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart newsletter-diaria.timer
