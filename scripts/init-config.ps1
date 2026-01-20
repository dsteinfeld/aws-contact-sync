#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Initialize AWS Contact Sync configuration in DynamoDB

.DESCRIPTION
    Creates the initial configuration in DynamoDB with default settings for
    contact synchronization and notifications.

.PARAMETER Profile
    AWS CLI profile to use (default: default)

.PARAMETER Region
    AWS region (default: us-east-1)

.PARAMETER StackName
    CloudFormation stack name (default: aws-contact-sync-prod)

.PARAMETER NotificationEmail
    Email address for notifications (optional)

.EXAMPLE
    bash scripts/init-config.sh --profile orgtest --region us-east-1 --email doug-orgmgmttest@thesteinfelds.org
#>

param(
    [string]$Profile = "default",
    [string]$Region = "us-east-1",
    [string]$StackName = "aws-contact-sync-prod",
    [string]$NotificationEmail = ""
)

# Get SNS topic ARN from CloudFormation stack
Write-Host "Retrieving SNS topic ARN from CloudFormation stack..."
$snsTopicArn = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --query "Stacks[0].Outputs[?OutputKey=='NotificationTopicArn'].OutputValue" `
    --output text `
    --profile $Profile `
    --region $Region

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to retrieve SNS topic ARN from stack"
    exit 1
}

Write-Host "SNS Topic ARN: $snsTopicArn"

# Get config table name
$configTableName = "${StackName}-config"
Write-Host "Config Table: $configTableName"

# Create configuration JSON
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")

$configData = @{
    contact_types = @("primary", "BILLING", "OPERATIONS", "SECURITY")
    excluded_accounts = @()
    retry_config = @{
        max_attempts = 3
        base_delay = 2.0
        max_delay = 60.0
        exponential_base = 2.0
    }
    notification_settings = @{
        user_notifications_config = @{
            notification_hub_region = $Region
            delivery_channels = @("EMAIL")
            notification_rules = @{}
        }
        fallback_sns_topic = $snsTopicArn
        notify_on_failure = $true
        notify_on_success = $false
        notify_on_partial_failure = $true
        failure_threshold = 1
    }
} | ConvertTo-Json -Depth 10 -Compress

$item = @{
    config_key = @{ S = "current" }
    config_data = @{ S = $configData }
    created_at = @{ S = $timestamp }
    updated_at = @{ S = $timestamp }
    version = @{ N = "1" }
} | ConvertTo-Json -Depth 10 -Compress

Write-Host "`nCreating configuration in DynamoDB..."
Write-Host "Configuration:"
Write-Host $configData

# Put item in DynamoDB
aws dynamodb put-item `
    --table-name $configTableName `
    --item $item `
    --profile $Profile `
    --region $Region

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n✓ Configuration created successfully!"
    
    # Verify the configuration
    Write-Host "`nVerifying configuration..."
    aws dynamodb get-item `
        --table-name $configTableName `
        --key '{\"config_key\":{\"S\":\"current\"}}' `
        --profile $Profile `
        --region $Region
    
    if ($NotificationEmail) {
        Write-Host "`nNote: To receive email notifications, you must confirm the SNS subscription sent to $NotificationEmail"
    }
} else {
    Write-Error "Failed to create configuration"
    exit 1
}
