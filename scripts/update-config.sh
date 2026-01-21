#!/bin/bash
# Update configuration in DynamoDB

PROFILE="orgtest"
REGION="us-east-1"
STACK_NAME="aws-contact-sync-prod"
CONFIG_TABLE="${STACK_NAME}-config"
SNS_TOPIC="arn:aws:sns:${REGION}:889662168126:${STACK_NAME}-notifications"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <config-type> [account-id]"
    echo ""
    echo "Config types:"
    echo "  full          - Sync all contact types to all accounts"
    echo "  billing-only  - Sync BILLING contact type only"
    echo "  exclude       - Exclude one account (requires account-id)"
    echo ""
    exit 1
fi

CONFIG_TYPE=$1
ACCOUNT_ID=$2

# Create config JSON based on type
case $CONFIG_TYPE in
    full)
        CONTACT_TYPES='["primary","BILLING","OPERATIONS","SECURITY"]'
        EXCLUDED='[]'
        ;;
    billing-only)
        CONTACT_TYPES='["BILLING"]'
        EXCLUDED='[]'
        ;;
    exclude)
        if [ -z "$ACCOUNT_ID" ]; then
            echo "Error: account-id required for exclude config"
            exit 1
        fi
        CONTACT_TYPES='["primary","BILLING","OPERATIONS","SECURITY"]'
        EXCLUDED="[\"$ACCOUNT_ID\"]"
        ;;
    *)
        echo "Error: Unknown config type: $CONFIG_TYPE"
        exit 1
        ;;
esac

# Create the full config JSON
cat > /tmp/config.json <<EOF
{
  "contact_types": $CONTACT_TYPES,
  "excluded_accounts": $EXCLUDED,
  "retry_config": {
    "max_attempts": 3,
    "base_delay": 2.0,
    "max_delay": 60.0,
    "exponential_base": 2.0
  },
  "notification_settings": {
    "user_notifications_config": {
      "notification_hub_region": "$REGION",
      "delivery_channels": ["EMAIL"],
      "notification_rules": {}
    },
    "fallback_sns_topic": "$SNS_TOPIC",
    "notify_on_failure": true,
    "notify_on_success": false,
    "notify_on_partial_failure": true,
    "failure_threshold": 1
  }
}
EOF

# Escape the JSON for DynamoDB
CONFIG_JSON=$(cat /tmp/config.json | python3 -c 'import json, sys; print(json.dumps(sys.stdin.read()))')

# Update DynamoDB
echo "Updating configuration..."
echo "  Contact Types: $CONTACT_TYPES"
echo "  Excluded Accounts: $EXCLUDED"
echo ""

aws dynamodb update-item \
    --table-name "$CONFIG_TABLE" \
    --key '{"config_key":{"S":"current"}}' \
    --update-expression "SET config_data = :config, updated_at = :updated" \
    --expression-attribute-values "{
        \":config\": {\"S\": $CONFIG_JSON},
        \":updated\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null || echo '2026-01-21T00:00:00.000Z')\"}
    }" \
    --profile "$PROFILE" \
    --region "$REGION"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Configuration updated successfully!"
    echo ""
    echo "Current configuration:"
    aws dynamodb get-item \
        --table-name "$CONFIG_TABLE" \
        --key '{"config_key":{"S":"current"}}' \
        --profile "$PROFILE" \
        --region "$REGION" \
        --query 'Item.config_data.S' \
        --output text | python3 -m json.tool
else
    echo "Error: Failed to update configuration"
    exit 1
fi

# Cleanup
rm -f /tmp/config.json
