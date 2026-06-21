#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.deploy"
ENV_OUTPUT="${ROOT_DIR}/.env"
PUSH_SECRETS=true
POPULATE_ENV=true

usage() {
  cat <<EOF
Usage: $0 [options] [env-file]

Options:
  --local-only         Populate .env without uploading GitHub secrets.
  --no-populate-env   Upload GitHub secrets without updating .env.
  --env-output PATH   Write the populated env file to PATH.
  -h, --help          Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local-only)
      PUSH_SECRETS=false
      shift
      ;;
    --no-populate-env)
      POPULATE_ENV=false
      shift
      ;;
    --env-output)
      ENV_OUTPUT="${2:?Missing path for --env-output}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ENV_FILE="$1"
      shift
      ;;
  esac
done

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

repo=""
if [[ "${PUSH_SECRETS}" == true ]]; then
  repo="${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
fi

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

set_secret_if_present() {
  local name="$1"
  local value="${!name:-}"
  if [[ -n "${value}" ]]; then
    set_repo_secret "${name}" "${value}"
  else
    echo "Skipped empty repo secret: ${name}"
  fi
}

set_env_secret_if_present() {
  local env_name="$1"
  local secret_name="$2"
  local value="${!secret_name:-}"
  if [[ -n "${value}" ]]; then
    set_env_secret "${env_name}" "${secret_name}" "${value}"
  else
    echo "Skipped empty ${env_name} secret: ${secret_name}"
  fi
}

populate_env_file() {
  local template="${ROOT_DIR}/.env.example"
  local output="$1"
  local current="${ROOT_DIR}/.env"
  local tmp

  if [[ ! -f "${template}" ]]; then
    echo "Missing env template: ${template}" >&2
    exit 1
  fi

  tmp="$(mktemp)"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    if [[ "${line}" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      local key="${BASH_REMATCH[1]}"
      local value="${line#*=}"
      if [[ -n "${!key:-}" ]]; then
        value="${!key}"
      elif [[ -f "${current}" ]]; then
        local existing
        existing="$(grep -m1 -E "^${key}=" "${current}" || true)"
        if [[ -n "${existing}" ]]; then
          value="${existing#*=}"
        fi
      fi
      printf '%s=%s\n' "${key}" "${value}" >> "${tmp}"
    else
      printf '%s\n' "${line}" >> "${tmp}"
    fi
  done < "${template}"

  mv "${tmp}" "${output}"
  chmod 600 "${output}"
  echo "Populated ${output} from ${template} and ${ENV_FILE}"
}

if [[ "${POPULATE_ENV}" == true ]]; then
  populate_env_file "${ENV_OUTPUT}"
fi

if [[ "${PUSH_SECRETS}" != true ]]; then
  echo "Skipped GitHub secret upload (--local-only)."
  exit 0
fi

for secret_name in \
  LIVEKIT_API_KEY \
  LIVEKIT_API_SECRET \
  OPENROUTER_API_KEY \
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

for secret_name in \
  LIVEKIT_API_KEY \
  LIVEKIT_API_SECRET \
  OPENROUTER_API_KEY \
  OPENAI_API_KEY \
  SONIOX_API_KEY \
  VOBIZ_API_KEY \
  AWS_ACCESS_KEY_ID \
  AWS_SECRET_ACCESS_KEY \
  JWT_SECRET_KEY \
  INTERNAL_API_SHARED_SECRET
do
  set_secret_if_present "${secret_name}"
done

set_env_secret_if_present "production" "PROD_SSH_PRIVATE_KEY"
set_env_secret_if_present "production" "PROD_HOST"
set_env_secret_if_present "production" "PROD_PORT"
set_env_secret_if_present "production" "PROD_USER"
set_env_secret_if_present "production" "APP_BIND_PORT"
set_env_secret_if_present "production" "PUBLIC_PORT"
set_env_secret_if_present "production" "SERVER_NAME"

set_env_secret_if_present "staging" "STAGING_VPS_SSH_PRIVATE_KEY"
set_env_secret_if_present "staging" "STAGING_VPS_HOST"
set_env_secret_if_present "staging" "STAGING_VPS_PORT"
set_env_secret_if_present "staging" "STAGING_VPS_USER"
set_env_secret_if_present "staging" "APP_BIND_PORT"
set_env_secret_if_present "staging" "PUBLIC_PORT"
set_env_secret_if_present "staging" "SERVER_NAME"

echo "All secrets uploaded successfully."
