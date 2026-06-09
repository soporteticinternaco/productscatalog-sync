#!/bin/sh

chmod 0644 /etc/cron.d/mycron

printenv > /etc/environment

cron

cat /etc/cron.d/mycron

tail -f /var/log/cron.log