#!/bin/bash
# Simple configuration initialization script for Windows compatibility

PROFILE="orgtest"
REGION="us-east-1"
STACK_NAME="aws-contact-sync-prod"

echo "Retrieving SNS topic ARN..."
SNS_TOPIC_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" \
    --output text \
    --profile "$PROFILE" \
    --region "$REGION")

echo "SNS Topic ARN: $SNS_TOPIC_ARN"

CONFIG_TABLE_NAME="${STACK_NAME}-config"
echo "Config Table: $CONFIG_TABLE_NAME"

# Create configuration using AWS CLI directly
echo "Creating configuration..."

aws dynamodb put-item \
    --table-name "$CONFIG_TABLE_NAME" \
    --item '{
        "config_key": {"S": "current"},
        "config_data": {"S": "{\"contact_types\":[\"primary\",\"BILLING\",\"OPERATIONS\",\"SECURITY\"],\"excluded_accounts\":[],\"retry_config\":{\"max_attempts\":3,\"base_delay\":2.0,\"max_delay\":60.0,\"exponential_base\":2.0},\"notification_settings\":{\"user_notifications_config\":{\"notification_hub_region\":\"'$REGION'\",\"delivery_channels\":[\"EMAIL\"],\"notification_rules\":{}},\"fallback_sns_topic\":\"'$SNS_TOPIC_ARN'\",\"notify_on_failure\":true,\"notify_on_success\":false,\"notify_on_partial_failure\":true,\"failure_threshold\":1}}"},
        "created_at": {"S": "2026-01-20T00:00:00.000Z"},
        "updated_at": {"S": "2026-01-20T00:00:00.000Z"},
        "version": {"N": "1"}
    }' \
    --profile "$PROFILE" \
    --region "$REGION"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Configuration created successfully!"
    echo ""
    echo "Verifying configuration..."
    aws dynamodb get-item \
        --table-name "$CONFIG_TABLE_NAME" \
        --key '{"config_key":{"S":"current"}}' \
        --profile "$PROFILE" \
        --region "$REGION"
else
    echo "Error: Failed to create configuration"
    exit 1
fi
