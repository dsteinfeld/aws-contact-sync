#!/bin/bash
#
# Initialize AWS Contact Sync configuration in DynamoDB
#
# Usage: bash scripts/init-config.sh [OPTIONS]
#
# Options:
#   -p, --profile PROFILE       AWS CLI profile to use (default: default)
#   -r, --region REGION         AWS region (default: us-east-1)
#   -s, --stack STACK_NAME      CloudFormation stack name (default: aws-contact-sync-prod)
#   -e, --email EMAIL           Email address for notifications (optional)
#   -h, --help                  Show this help message

set -e

# Default values
PROFILE="default"
REGION="us-east-1"
STACK_NAME="aws-contact-sync-prod"
NOTIFICATION_EMAIL=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--profile)
            PROFILE="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -s|--stack)
            STACK_NAME="$2"
            shift 2
            ;;
        -e|--email)
            NOTIFICATION_EMAIL="$2"
            shift 2
            ;;
        -h|--help)
            grep '^#' "$0" | cut -c 3-
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Set AWS CLI options
AWS_CLI_OPTS=()
if [ -n "$PROFILE" ]; then
    AWS_CLI_OPTS+=(--profile "$PROFILE")
fi
if [ -n "$REGION" ]; then
    AWS_CLI_OPTS+=(--region "$REGION")
fi

echo "Retrieving SNS topic ARN from CloudFormation stack..."
SNS_TOPIC_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" \
    --output text \
    "${AWS_CLI_OPTS[@]}")

if [ -z "$SNS_TOPIC_ARN" ]; then
    echo "Error: Failed to retrieve SNS topic ARN from stack"
    exit 1
fi

echo "SNS Topic ARN: $SNS_TOPIC_ARN"

# Get config table name
CONFIG_TABLE_NAME="${STACK_NAME}-config"
echo "Config Table: $CONFIG_TABLE_NAME"

# Create timestamp
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

# Create configuration JSON
CONFIG_DATA=$(cat <<EOF
{
  "contact_types": ["primary", "BILLING", "OPERATIONS", "SECURITY"],
  "excluded_accounts": [],
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
    "fallback_sns_topic": "$SNS_TOPIC_ARN",
    "notify_on_failure": true,
    "notify_on_success": false,
    "notify_on_partial_failure": true,
    "failure_threshold": 1
  }
}
EOF
)

# Escape JSON for DynamoDB
CONFIG_DATA_ESCAPED=$(echo "$CONFIG_DATA" | jq -c .)

# Create DynamoDB item
ITEM=$(cat <<EOF
{
  "config_key": {"S": "current"},
  "config_data": {"S": $CONFIG_DATA_ESCAPED},
  "created_at": {"S": "$TIMESTAMP"},
  "updated_at": {"S": "$TIMESTAMP"},
  "version": {"N": "1"}
}
EOF
)

echo ""
echo "Creating configuration in DynamoDB..."
echo "Configuration:"
echo "$CONFIG_DATA" | jq .

# Put item in DynamoDB
aws dynamodb put-item \
    --table-name "$CONFIG_TABLE_NAME" \
    --item "$ITEM" \
    "${AWS_CLI_OPTS[@]}"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Configuration created successfully!"
    
    # Verify the configuration
    echo ""
    echo "Verifying configuration..."
    aws dynamodb get-item \
        --table-name "$CONFIG_TABLE_NAME" \
        --key '{"config_key":{"S":"current"}}' \
        "${AWS_CLI_OPTS[@]}" | jq .
    
    if [ -n "$NOTIFICATION_EMAIL" ]; then
        echo ""
        echo "Note: To receive email notifications, you must confirm the SNS subscription sent to $NOTIFICATION_EMAIL"
    fi
else
    echo "Error: Failed to create configuration"
    exit 1
fi
