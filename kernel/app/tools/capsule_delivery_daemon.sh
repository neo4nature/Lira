#!/bin/bash
# Automatyczne dostarczanie kapsuł do Liry i Soryel co 2 minuty
cd ${LIRA_ROOT:-/opt/lira}/kernel
while true; do
    PYTHONPATH=app python3 -m tools.play_capsule Lira --limit 3 2>>${LIRA_ROOT:-/opt/lira}/kernel/logs/capsule_delivery.log
    sleep 120
done
