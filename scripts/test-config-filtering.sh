#!/bin/bash
# Test configuration filtering functionality

PROFILE="orgtest"
REGION="us-east-1"
STACK_NAME="aws-contact-sync-prod"
CONFIG_TABLE="${STACK_NAME}-config"

echo "=== Configuration Filtering Test Script ==="
echo ""

# Function to update config
update_config() {
    local contact_types=$1
    local excluded_accounts=$2
    
    echo "Updating configuration..."
    echo "  Contact Types: $contact_types"
    echo "  Excluded Accounts: $excluded_accounts"
    
    aws dynamodb update-item \
        --table-name "$CONFIG_TABLE" \
        --key '{"config_key":{"S":"current"}}' \
        --update-expression "SET config_data = :config, updated_at = :updated" \
        --expression-attribute-values "{
            \":config\": {\"S\": \"{\\\"contact_types\\\":$contact_types,\\\"excluded_accounts\\\":$excluded_accounts,\\\"retry_config\\\":{\\\"max_attempts\\\":3,\\\"base_delay\\\":2.0,\\\"max_delay\\\":60.0,\\\"exponential_base\\\":2.0},\\\"notification_settings\\\":{\\\"user_notifications_config\\\":{\\\"notification_hub_region\\\":\\\"$REGION\\\",\\\"delivery_channels\\\":[\\\"EMAIL\\\"],\\\"notification_rules\\\":{}},\\\"fallback_sns_topic\\\":\\\"arn:aws:sns:$REGION:889662168126:${STACK_NAME}-notifications\\\",\\\"notify_on_failure\\\":true,\\\"notify_on_success\\\":false,\\\"notify_on_partial_failure\\\":true,\\\"failure_threshold\\\":1}}\"},
            \":updated\": {\"S\": \"$(date -u +%Y-%m-%dT%H:%M:%S.000Z)\"}
        }" \
        --profile "$PROFILE" \
        --region "$REGION"
    
    echo "Configuration updated!"
    echo ""
}

# Function to view current config
view_config() {
    echo "Current configuration:"
    aws dynamodb get-item \
        --table-name "$CONFIG_TABLE" \
        --key '{"config_key":{"S":"current"}}' \
        --profile "$PROFILE" \
        --region "$REGION" \
        --query 'Item.config_data.S' \
        --output text | python3 -m json.tool
    echo ""
}

# Test scenarios
echo "Choose a test scenario:"
echo "1. Reset to full sync (all contact types, no exclusions)"
echo "2. Filter to BILLING only"
echo "3. Exclude one account (you'll need to provide account ID)"
echo "4. View current configuration"
echo ""
read -p "Enter choice (1-4): " choice

case $choice in
    1)
        update_config '["primary","BILLING","OPERATIONS","SECURITY"]' '[]'
        view_config
        echo "✓ Configuration reset to sync all contact types to all accounts"
        ;;
    2)
        update_config '["BILLING"]' '[]'
        view_config
        echo "✓ Configuration set to sync BILLING only"
        echo "Now make an OPERATIONS contact change - it should NOT trigger sync"
        echo "Then make a BILLING contact change - it SHOULD trigger sync"
        ;;
    3)
        read -p "Enter account ID to exclude: " account_id
        update_config '["primary","BILLING","OPERATIONS","SECURITY"]' "[\"$account_id\"]"
        view_config
        echo "✓ Configuration set to exclude account $account_id"
        echo "Now make a contact change - account $account_id should NOT be updated"
        ;;
    4)
        view_config
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac
