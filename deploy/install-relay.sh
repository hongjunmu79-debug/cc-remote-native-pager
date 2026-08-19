#!/usr/bin/env bash
# First-install/update entrypoint for a role-scoped relay release bundle.
set -euo pipefail

die() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  echo "usage: install-relay.sh BUNDLE --domain remote.example.com" >&2
  exit 2
}

bundle="${1:-}"
[ -n "$bundle" ] || usage
shift
domain=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain)
      [ "$#" -ge 2 ] || usage
      domain="$2"
      shift 2
      ;;
    *) die "unknown relay installer argument: $1" ;;
  esac
done
[ -n "$domain" ] || usage
domain="$(printf '%s' "$domain" | tr '[:upper:]' '[:lower:]')"
case "$domain" in
  *[!a-z0-9.-]*|*..*|.*|*.) die "domain must be a DNS hostname without a scheme or path" ;;
esac
case "$domain" in
  *.*) ;;
  *) die "domain must be a DNS hostname such as remote.example.com" ;;
esac

[ "$(id -u)" -eq 0 ] || die "relay installation must run as root"
[ "$(uname -s)" = Linux ] || die "relay installation requires Linux"
[ -r /etc/os-release ] || die "/etc/os-release is required"
# shellcheck disable=SC1091
source /etc/os-release
case "${ID:-}" in
  ubuntu) minimum_major=22 ;;
  debian) minimum_major=12 ;;
  *) die "relay supports Ubuntu 22.04+ and Debian 12+" ;;
esac
os_major="${VERSION_ID%%.*}"
case "$os_major" in
  ""|*[!0-9]*) die "/etc/os-release has an invalid VERSION_ID" ;;
esac
os_major=$((10#$os_major))
[ "$os_major" -ge "$minimum_major" ] || \
  die "relay supports Ubuntu 22.04+ and Debian 12+"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v openssl >/dev/null 2>&1 || die "openssl is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

case "$(uname -m)" in
  x86_64|amd64) machine=x86_64 ;;
  arm64|aarch64) machine=arm64 ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac
bundle="$(cd "$bundle" && pwd -P)"
(
  cd "$bundle"
  python3 -m deploy.release_manifest \
    "$bundle" --role relay --os linux --arch "$machine"
)

appdir=/opt/cc-remote
env_file="$appdir/.env"
new_env=""
cleanup() {
  [ -z "$new_env" ] || rm -f -- "$new_env"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

if [ ! -e "$env_file" ]; then
  password=""
  if [ -n "${CC_REMOTE_LOGIN_PASSWORD_FILE:-}" ]; then
    password_file="$CC_REMOTE_LOGIN_PASSWORD_FILE"
    if [ ! -f "$password_file" ] || [ -L "$password_file" ]; then
      die "CC_REMOTE_LOGIN_PASSWORD_FILE must be a regular file"
    fi
    IFS= read -r password < "$password_file" || true
  else
    [ -t 0 ] || die \
      "interactive login password required (or set CC_REMOTE_LOGIN_PASSWORD_FILE)"
    printf 'Web login password (minimum 16 characters): ' >&2
    IFS= read -r -s password
    printf '\nRepeat web login password: ' >&2
    IFS= read -r -s password_repeat
    printf '\n' >&2
    [ "$password" = "$password_repeat" ] || die "login passwords do not match"
  fi
  [ "${#password}" -ge 16 ] || die "login password must be at least 16 characters"
  [ "${#password}" -le 1024 ] || die "login password must be at most 1024 characters"
  if printf '%s' "$password" | LC_ALL=C grep -q '[[:cntrl:]]'; then
    die "login password cannot contain control characters"
  fi
  case "$password" in
    *"'"*|*\\*) die "login password cannot contain a single quote or backslash" ;;
  esac

  session_secret="$(openssl rand -hex 32)"
  wrapper_token="$(openssl rand -hex 32)"
  [ "${#session_secret}" -eq 64 ] || die "could not generate SESSION_SECRET"
  [ "${#wrapper_token}" -eq 64 ] || die "could not generate WRAPPER_TOKEN"

  install -d -o root -g root -m 0755 "$appdir"
  umask 077
  new_env="$(mktemp "$appdir/.env.new.XXXXXX")"
  {
    printf '%s\n' \
      'RELAY_HOST=127.0.0.1' \
      'RELAY_PORT=8765' \
      "PUBLIC_ORIGIN=https://${domain}" \
      "LOGIN_PASSWORD='${password}'" \
      "SESSION_SECRET=${session_secret}" \
      "WRAPPER_TOKEN=${wrapper_token}" \
      'WEB_STATIC_DIR=/opt/cc-remote/current/web/dist' \
      'PUSH_DB_PATH=/opt/cc-remote/state/relay-push.sqlite3' \
      'DEVICE_DB_PATH=/opt/cc-remote/state/relay-devices.sqlite3' \
      'LOG_LEVEL=INFO'
  } > "$new_env"
  install -o root -g root -m 0600 "$new_env" "$env_file"
  rm -f -- "$new_env"
  new_env=""
  unset password password_repeat session_secret wrapper_token
  echo "==> created root-only relay configuration"
else
  if [ ! -f "$env_file" ] || [ -L "$env_file" ]; then
    die "$env_file must be a regular file"
  fi
  echo "==> preserving existing relay configuration"
fi

bash "$bundle/deploy/setup-vps.sh" "$domain" "$bundle"

echo
echo "Relay installed. Open https://$domain/ and log in."
echo "Then open Devices, create a one-time pairing code, and run the"
echo "wrapper installer on the Mac or Linux machine that hosts Claude/Codex."
