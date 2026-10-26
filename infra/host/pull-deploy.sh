#!/bin/sh
# Pull-based deploy, run by frontdesk-deploy.timer every 2 minutes (installed by cloud-init).
#
# GitHub Actions can't reach AWS from this account (no OIDC providers allowed), so the host deploys
# itself: pull $IMAGE_REPO:$DEPLOY_TAG, and when its digest differs from the one running, copy deploy/
# out of that image and run its deploy.sh. If that fails, remember the digest as failed (so a broken
# release isn't retried every 2 minutes) and redeploy the last good digest.
#
# Usage: pull-deploy.sh [--force]   --force re-runs the deployed digest, e.g. after changing a parameter
#
# State in $FRONTDESK_HOME/state: deployed-digest, failed-digest, history.log. Output goes to journald
# (journalctl -u frontdesk-deploy). This script is the trust anchor and lives outside the image;
# changing it needs a Session Manager shell (or a new instance).
set -eu

IMAGE_REPO=${IMAGE_REPO:-ghcr.io/shomas383/frontdesk-ai}
DEPLOY_TAG=${DEPLOY_TAG:-prod}
FRONTDESK_HOME=${FRONTDESK_HOME:-/opt/frontdesk}
LOCK_FILE=${LOCK_FILE:-/run/frontdesk-deploy.lock}
STATE=$FRONTDESK_HOME/state
export FRONTDESK_HOME

log() { echo "pull-deploy: $*"; }
record() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >>"$STATE/history.log"; }

# One run at a time, whether started by the timer or by hand.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "another deploy is running; skipping"
    exit 0
fi

force=false
[ "${1:-}" = "--force" ] && force=true
mkdir -p "$STATE"
deployed=$(cat "$STATE/deployed-digest" 2>/dev/null || true)
failed=$(cat "$STATE/failed-digest" 2>/dev/null || true)

version_of() {
    docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$1" 2>/dev/null || echo "?"
}

# Copy deploy/ out of the image into $FRONTDESK_HOME/release and run its deploy.sh.
# Called as an `if` condition, where `set -e` is off, so every step returns explicitly.
run_release() {
    image=$1
    docker pull -q "$image" >/dev/null || return 1
    tmp=$(mktemp -d "$FRONTDESK_HOME/release.XXXXXX") || return 1
    cid=$(docker create "$image") || { rm -rf "$tmp"; return 1; }
    docker cp "$cid:/app/deploy/." "$tmp/"
    copied=$?
    docker rm "$cid" >/dev/null
    [ "$copied" -eq 0 ] || { rm -rf "$tmp"; return 1; }
    rm -rf "$FRONTDESK_HOME/release" && mv "$tmp" "$FRONTDESK_HOME/release" || return 1
    sh "$FRONTDESK_HOME/release/deploy.sh" "$image"
}

if $force && [ -n "$deployed" ]; then
    target=$deployed
else
    if ! out=$(docker pull -q "$IMAGE_REPO:$DEPLOY_TAG" 2>&1); then
        log "cannot pull $IMAGE_REPO:$DEPLOY_TAG (none pushed yet, or registry unreachable): $out"
        exit 0
    fi
    target=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE_REPO:$DEPLOY_TAG" \
        | grep "^$IMAGE_REPO@" | head -n 1)
    [ -n "$target" ] || { log "no digest for $IMAGE_REPO:$DEPLOY_TAG"; exit 1; }
    if ! $force; then
        [ "$target" = "$deployed" ] && exit 0  # already running
        [ "$target" = "$failed" ] && exit 0    # failed before; wait for a new :$DEPLOY_TAG
    fi
fi

version=$(version_of "$target")
log "deploying $target ($version)"
if run_release "$target"; then
    echo "$target" >"$STATE/deployed-digest"
    rm -f "$STATE/failed-digest"
    record "deployed $target $version"
    log "deployed $version"
    exit 0
fi

echo "$target" >"$STATE/failed-digest"
record "failed $target $version"
log "deploy of $version FAILED"
if [ -n "$deployed" ] && [ "$deployed" != "$target" ]; then
    previous=$(version_of "$deployed")
    log "rolling back to $deployed ($previous)"
    if run_release "$deployed"; then
        record "rolled-back $deployed $previous"
        log "rolled back to $previous"
    else
        record "rollback-failed $deployed $previous"
        log "ROLLBACK FAILED; the site may be down"
    fi
fi
exit 1
