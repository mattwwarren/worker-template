#!/usr/bin/env bash
set -euo pipefail

# Idempotent Redis secret generator
# Creates redis-secret in the namespace if it doesn't exist

NAMESPACE="${NAMESPACE:-warren-enterprises-ltd}"
SECRET_NAME="redis-secret"

# Check if secret already exists
if kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" &>/dev/null; then
  echo "Secret $SECRET_NAME already exists in $NAMESPACE"
  exit 0
fi

# Generate credentials
REDIS_PASSWORD="${REDIS_PASSWORD:-}"

# Create secret
kubectl create secret generic "$SECRET_NAME" \
  --namespace="$NAMESPACE" \
  --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD"

echo "Created secret $SECRET_NAME in $NAMESPACE"
