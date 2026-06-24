#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

provider="${1:-deepseek}"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and fill in your keys first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

case "$provider" in
  baseline|foundry)
    default_model="gpt-5.4"
    default_endpoint="https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1"
    default_auth="api-key"
    ;;
  doubao)
    default_model="doubao-seed-2-0-lite-260428"
    default_endpoint="https://ark.cn-beijing.volces.com/api/v3"
    default_auth="bearer"
    ;;
  deepseek)
    default_model="DeepSeek-V4-Flash"
    default_endpoint="https://<your-foundry-resource>.services.ai.azure.com/openai/v1/"
    default_auth="bearer"
    ;;
  kimi)
    default_model="Kimi-K2.6"
    default_endpoint="https://<your-foundry-resource>.services.ai.azure.com/openai/v1/"
    default_auth="bearer"
    ;;
  minimax)
    default_model="MiniMax-M2.7"
    default_endpoint="https://api.minimaxi.com/v1"
    default_auth="bearer"
    ;;
  *)
    echo "Unknown provider: $provider" >&2
    echo "Use one of: baseline, doubao, deepseek, kimi, minimax" >&2
    exit 1
    ;;
esac

upper_provider="$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')"
model_var="BYOM_${upper_provider}_MODEL_TYPE"
endpoint_var="BYOM_${upper_provider}_ENDPOINT"

model_type="${!model_var:-$default_model}"
endpoint="${!endpoint_var:-$default_endpoint}"
auth_scheme="${BYOM_AUTH_SCHEME:-$default_auth}"

echo "Running Voice Live BYOM demo"
echo "Provider: $provider"
echo "Model/deployment: $model_type"
echo "Endpoint: $endpoint"
echo "Auth: $auth_scheme"

python byom_demo.py \
  --provider "$provider" \
  --model "$model_type" \
  --byom "byom-chat-completion" \
  --byom-endpoint "$endpoint" \
  --byom-model-type "$model_type" \
  --byom-auth-scheme "$auth_scheme" \
  --no-proactive-greeting \
  --verbose
