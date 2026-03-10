#!/usr/bin/env bash
set -euo pipefail

# Idempotent RabbitMQ secret generator
# Creates rabbitmq-secret in the namespace if it doesn't exist

NAMESPACE="${NAMESPACE:-warren-enterprises-ltd}"
SECRET_NAME="rabbitmq-secret"

# Check if secret already exists
if kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" &>/dev/null; then
  echo "Secret $SECRET_NAME already exists in $NAMESPACE"
  exit 0
fi

# Generate credentials
RABBITMQ_USER="${RABBITMQ_USER:-guest}"
RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD:-guest}"

# Create secret
kubectl create secret generic "$SECRET_NAME" \
  --namespace="$NAMESPACE" \
  --from-literal=RABBITMQ_USER="$RABBITMQ_USER" \
  --from-literal=RABBITMQ_PASSWORD="$RABBITMQ_PASSWORD"

echo "Created secret $SECRET_NAME in $NAMESPACE"
