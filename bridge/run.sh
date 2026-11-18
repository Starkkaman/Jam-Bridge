#!/command/with-contenv bashio
# shellcheck shell=bash
# Starts The Bridge for Home Assistant Ingress (port 8099, see config.yaml). The server only accepts the
# Supervisor's ingress proxy (172.30.32.2) and loopback. After a restore, the database comes back from the
# backup snapshot (app/backup.py --restore; a no-op when /data/bridge.db exists).
bashio::log.info "Starting The Bridge"
python3 /app/backup.py --restore || bashio::log.warning "Snapshot restore failed; starting with an empty database"
cd / || exit 1
exec python3 -u -m app --host 0.0.0.0 --port 8099
