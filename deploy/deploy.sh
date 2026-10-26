#!/bin/sh
# Deploy one release on the EC2 host: render the env file from Parameter Store, start the stack,
# and wait until the app is healthy. Exits non-zero if it isn't, so the caller can roll back.
#
# Usage: deploy.sh <image@sha256:...>
#
# Called by /opt/frontdesk/pull-deploy.sh (infra/host/) from the copy of deploy/ it extracted out of
# that same image, so this script, compose.prod.yml and the Caddyfile are versioned with the release.
#
# Environment (all optional):
#   FRONTDESK_HOME    where .env lives (default /opt/frontdesk)
#   PARAM_PATH        Parameter Store path (default /frontdesk-ai/prod)
#   AWS_REGION        default us-east-2
#   HEALTH_TIMEOUT    seconds to wait for a healthy app (default 900)
#   PARAMS_FILE       local testing only: read "NAME<TAB>value" lines from this file instead of SSM
#   COMPOSE_OVERRIDE  local testing only: an extra compose file (e.g. json-file logging, other ports)
set -eu

IMAGE=${1:?usage: deploy.sh <image@sha256:...>}
DIR=$(cd "$(dirname "$0")" && pwd)
FRONTDESK_HOME=${FRONTDESK_HOME:-/opt/frontdesk}
PARAM_PATH=${PARAM_PATH:-/frontdesk-ai/prod}
AWS_REGION=${AWS_REGION:-us-east-2}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-900}
ENV_FILE=$FRONTDESK_HOME/.env

log() { echo "deploy: $*"; }
fail() { echo "deploy: ERROR: $*" >&2; exit 1; }

fetch_params() {
    if [ -n "${PARAMS_FILE:-}" ]; then
        cat "$PARAMS_FILE"
    else
        aws ssm get-parameters-by-path --region "$AWS_REGION" --path "$PARAM_PATH" \
            --with-decryption --query 'Parameters[*].[Name,Value]' --output text
    fi
}

# Render the env file: one NAME='value' line per parameter (single quotes, so compose doesn't
# interpolate '$' in secrets), plus APP_IMAGE. Written 0600 and moved into place atomically.
mkdir -p "$FRONTDESK_HOME"
tmp=$(mktemp "$FRONTDESK_HOME/.env.XXXXXX")
trap 'rm -f "$tmp"' EXIT
chmod 600 "$tmp"
params=$(fetch_params) || fail "could not read parameters under $PARAM_PATH"
tab=$(printf '\t')
printf '%s\n' "$params" | while IFS="$tab" read -r name value; do
    [ -n "$name" ] || continue
    name=${name##*/}
    case $name in
        *[!A-Z0-9_]* | [0-9]*) fail "parameter name '$name' is not a valid env var name" ;;
    esac
    case $value in
        *"'"*) fail "parameter $name contains a single quote" ;;
    esac
    printf "%s='%s'\n" "$name" "$value"
done >"$tmp"
for required in ANTHROPIC_API_KEY POSTGRES_PASSWORD; do
    grep -q "^$required=" "$tmp" || fail "$PARAM_PATH/$required is missing"
done
# The password is embedded in DATABASE_URL, so it must not need URL escaping.
grep -Eq "^POSTGRES_PASSWORD='[A-Za-z0-9._~-]+'$" "$tmp" \
    || fail "POSTGRES_PASSWORD may only contain letters, digits and . _ ~ -"
printf "APP_IMAGE='%s'\n" "$IMAGE" >>"$tmp"
mv "$tmp" "$ENV_FILE"
trap - EXIT

compose() {
    if [ -n "${COMPOSE_OVERRIDE:-}" ]; then
        docker compose -f "$DIR/compose.prod.yml" -f "$COMPOSE_OVERRIDE" --env-file "$ENV_FILE" "$@"
    else
        docker compose -f "$DIR/compose.prod.yml" --env-file "$ENV_FILE" "$@"
    fi
}

log "starting $IMAGE"
compose up -d --remove-orphans

# Wait for the app's healthcheck (ingest + model load take a few minutes on a t3.small).
app=$(compose ps -q app)
[ -n "$app" ] || fail "app container not found"
deadline=$(($(date +%s) + HEALTH_TIMEOUT))
while :; do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$app")
    case $status in
        healthy) break ;;
        unhealthy | exited | dead) fail "app is $status" ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || fail "app not healthy after ${HEALTH_TIMEOUT}s (status: $status)"
    sleep 5
done
log "app is healthy"

# Remove untagged images (e.g. the previous :prod); a rollback pulls them again from GHCR.
docker image prune -f >/dev/null
log "done"
