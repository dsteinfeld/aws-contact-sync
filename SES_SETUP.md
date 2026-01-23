# SES Cross-Account Setup Guide

This guide explains how to configure cross-account SES access for the AWS Contact Sync notification system.

## Overview

The notification system sends emails directly to the Security alternate contact using AWS SES (Simple Email Service). Since SES is already configured in another AWS account within your organization, we use cross-account IAM role assumption to access it.

## Architecture

```
Management Account (889662168126)
  └─> NotificationHandler Lambda
       └─> Assumes Role in SES Account
            └─> Sends email via SES

SES Account (Your existing SES account)
  └─> IAM Role: ContactSyncSESRole
       └─> Permissions: ses:SendEmail, ses:SendRawEmail
       └─> Trust Policy: Allows Management Account Lambda role
```

## Setup Steps

### Step 1: Create IAM Role in SES Account

In the AWS account where SES is configured, create an IAM role:

**Role Name:** `ContactSyncSESRole` (or your preferred name)

**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::889662168126:role/aws-contact-sync-prod-NotificationHandlerRole-*"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "StringEquals": {
          "sts:ExternalId": "contact-sync-ses-access"
        }
      }
    }
  ]
}
```

**Permissions Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail"
      ],
      "Resource": "*"
    }
  ]
}
```

### Step 2: Note the Role ARN

After creating the role, note its ARN. It will look like:
```
arn:aws:iam::<SES_ACCOUNT_ID>:role/ContactSyncSESRole
```

### Step 3: Deploy with SES Parameters

When deploying the stack, provide the SES parameters:

```bash
sam deploy \
  --parameter-overrides \
    ManagementAccountId=889662168126 \
    SESAccountId=<YOUR_SES_ACCOUNT_ID> \
    SESRoleName=ContactSyncSESRole \
    SESSenderDomain=<YOUR_VERIFIED_DOMAIN>
```

Example:
```bash
sam deploy \
  --parameter-overrides \
    ManagementAccountId=889662168126 \
    SESAccountId=123456789012 \
    SESRoleName=ContactSyncSESRole \
    SESSenderDomain=example.com
```

### Step 4: Verify SES Domain

Ensure your domain is verified in SES in the SES account. The sender email will be:
```
AWS Contact Sync <noreply@your-domain.com>
```

## How It Works

1. **Sync completes**: All accounts finish processing
2. **Lambda triggered**: DynamoDB Stream triggers NotificationHandler
3. **Get Security contact**: Lambda retrieves Security alternate contact email
4. **Assume SES role**: Lambda assumes role in SES account
5. **Send email**: Lambda sends email directly to Security contact via SES
6. **Fallback to SNS**: If SES fails or no Security contact, uses SNS topic

## Benefits

✅ **No subscription management**: Emails sent directly, no confirmation needed
✅ **Automatic updates**: When Security contact changes, next email goes to new address
✅ **Centralized SES**: Use existing SES configuration
✅ **Secure**: Cross-account access via IAM roles
✅ **Reliable fallback**: SNS topic as backup delivery method

## Troubleshooting

### Lambda can't assume role
- Check trust policy in SES account role
- Verify role ARN is correct in stack parameters
- Check Lambda execution role has `sts:AssumeRole` permission

### SES emails not sending
- Verify domain in SES account
- Check SES is out of sandbox (or recipient is verified)
- Review CloudWatch logs for SES errors
- Verify role has `ses:SendEmail` permission

### No emails received
- Check Security alternate contact is configured
- Verify email address is correct
- Check spam/junk folders
- Review CloudWatch logs for delivery status

## Configuration Reference

### Stack Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `SESAccountId` | AWS account ID where SES is configured | `123456789012` |
| `SESRoleName` | IAM role name in SES account | `ContactSyncSESRole` |
| `SESSenderDomain` | Verified domain in SES | `example.com` |

### Environment Variables (Auto-configured)

| Variable | Description |
|----------|-------------|
| `SES_ROLE_ARN` | Full ARN of role to assume |
| `SES_SENDER_DOMAIN` | Domain for sender email address |

## Alternative: Same-Account SES

If you want to use SES in the management account instead:

1. Don't provide `SESAccountId` parameter (or set to management account ID)
2. Verify domain in management account SES
3. Lambda will use local SES without role assumption
4. Update NotificationHandler role to include SES permissions directly

## Testing

Test email delivery:
```bash
aws lambda invoke \
  --function-name aws-contact-sync-prod-notification-handler \
  --payload '{"notification_type": "test_delivery"}' \
  response.json
```

Check CloudWatch logs for delivery status.
