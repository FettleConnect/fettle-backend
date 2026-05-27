#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-${ROOT_DIR}/.env.deploy}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

repo="${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

require_value() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required value: ${name}" >&2
    exit 1
  fi
}

set_repo_secret() {
  local name="$1"
  local value="$2"
  printf '%s' "${value}" | gh secret set "${name}" --repo "${repo}"
  echo "Set repo secret: ${name}"
}

set_env_secret() {
  local env_name="$1"
  local secret_name="$2"
  local value="$3"
  printf '%s' "${value}" | gh secret set "${secret_name}" --repo "${repo}" --env "${env_name}"
  echo "Set ${env_name} secret: ${secret_name}"
}

for secret_name in \
  GHCR_USERNAME \
  GHCR_TOKEN \
  LIVEKIT_API_KEY \
  LIVEKIT_API_SECRET \
  OPENAI_API_KEY \
  PROD_SSH_PRIVATE_KEY \
  PROD_HOST \
  PROD_PORT \
  PROD_USER \
  STAGING_VPS_SSH_PRIVATE_KEY \
  STAGING_VPS_HOST \
  STAGING_VPS_PORT \
  STAGING_VPS_USER
  do
  require_value "${secret_name}"
done

set_repo_secret "GHCR_USERNAME" "${GHCR_USERNAME}"
set_repo_secret "GHCR_TOKEN" "${GHCR_TOKEN}"
set_repo_secret "LIVEKIT_API_KEY" "${LIVEKIT_API_KEY}"
set_repo_secret "LIVEKIT_API_SECRET" "${LIVEKIT_API_SECRET}"
set_repo_secret "OPENAI_API_KEY" "${OPENAI_API_KEY}"

set_env_secret "production" "PROD_SSH_PRIVATE_KEY" "${PROD_SSH_PRIVATE_KEY}"
set_env_secret "production" "PROD_HOST" "${PROD_HOST}"
set_env_secret "production" "PROD_PORT" "${PROD_PORT}"
set_env_secret "production" "PROD_USER" "${PROD_USER}"

set_env_secret "staging" "STAGING_VPS_SSH_PRIVATE_KEY" "${STAGING_VPS_SSH_PRIVATE_KEY}"
set_env_secret "staging" "STAGING_VPS_HOST" "${STAGING_VPS_HOST}"
set_env_secret "staging" "STAGING_VPS_PORT" "${STAGING_VPS_PORT}"
set_env_secret "staging" "STAGING_VPS_USER" "${STAGING_VPS_USER}"

echo "All secrets uploaded successfully."
