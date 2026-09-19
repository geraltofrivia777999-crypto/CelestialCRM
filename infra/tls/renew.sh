#!/bin/sh
set -eu
cd /opt/celestial-crm
exec 9>/run/lock/celestial-crm-tls-renew.lock
flock -n 9 || exit 0
docker run --rm \
  -v /opt/celestial-crm/infra/tls/letsencrypt:/etc/letsencrypt \
  -v /opt/celestial-crm/infra/tls/webroot:/var/www/certbot \
  -v /opt/celestial-crm/infra/tls/logs:/var/log/letsencrypt \
  -v /opt/celestial-crm/infra/tls/work:/var/lib/letsencrypt \
  certbot/certbot:v5.8.0 renew --quiet --cert-name celestialgroup.fit
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  -f docker-compose.tls.yml exec -T nginx nginx -t
docker compose -f docker-compose.yml -f docker-compose.production.yml \
  -f docker-compose.tls.yml exec -T nginx nginx -s reload
