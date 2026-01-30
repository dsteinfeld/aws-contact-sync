# AWS Contact Sync - New Organization Deployment Checklist

Use this checklist when deploying AWS Contact Sync to a new organization's management account.

## Pre-Deployment Information Gathering

### Required Information
Collect the following information before starting:

- [ ] **New Management Account ID**: `_________________________`
- [ ] **SES Account ID**: `_________________________`
- [ ] **SES Sender Domain**: `_________________________`
- [ ] **AWS Region**: `_________________________` (default: us-east-1)
- [ ] **Security Contact Email**: `_________________________`
- [ ] **AWS CLI Profile Name**: `_________________________`
- [ ] **Environment**: `_________________________` (dev/staging/prod)

### Optional Information
- [ ] **SNS Notification Email**: `_________________________`
- [ ] **Accounts to Exclude**: `_________________________`
- [ ] **Custom SES Role Name**: `_________________________` (default: OrgSES-SendingRole)

---

## Step 1: Prerequisites Setup

### 1.1 AWS CLI Configuration
- [ ] AWS CLI installed and updated
- [ ] Configure AWS CLI profile for new management account:
  ```bash
  aws configure --profile <profile-name>
  ```
- [ ] Verify credentials work:
  ```bash
  aws sts get-caller-identity --profile <profile-name>
  ```
- [ ] Confirm you're in the management account (check account ID)

### 1.2 SAM CLI
- [ ] SAM CLI installed
- [ ] Verify SAM CLI version:
  ```bash
  sam --version
  ```

### 1.3 Git Repository
- [ ] Clone or pull latest code:
  ```bash
  git pull origin main
  ```
- [ ] Verify you're on main branch:
  ```bash
  git branch
  ```

---

## Step 2: SES Account Configuration

### 2.1 Verify SES Domain
In the SES account:
- [ ] Log into SES account
- [ ] Navigate to SES console
- [ ] Verify domain is verified in SES
- [ ] Note the verified domain name: `_________________________`

### 2.2 Create/Update IAM Role
In the SES account, create or update the `OrgSES-SendingRole`:

- [ ] Navigate to IAM → Roles
- [ ] Create role named `OrgSES-SendingRole` (or verify it exists)
- [ ] Attach trust policy (replace `<NEW_MGMT_ACCOUNT_ID>` with actual ID):
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "AWS": "arn:aws:iam::<NEW_MGMT_ACCOUNT_ID>:role/aws-contact-sync-prod-NotificationHandlerRole-*"
        },
        "Action": "sts:AssumeRole"
      }
    ]
  }
  ```
- [ ] Attach permissions policy:
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
- [ ] Note the role ARN: `_________________________`

---

## Step 3: Deploy the Stack

### 3.1 Build and Deploy
- [ ] Navigate to project directory
- [ ] Run deployment command:
  ```bash
  bash scripts/deploy.sh \
    --environment prod \
    --account-id <MGMT_ACCOUNT_ID> \
    --ses-account-id <SES_ACCOUNT_ID> \
    --ses-sender-domain <DOMAIN> \
    --profile <PROFILE_NAME> \
    --region us-east-1 \
    --no-confirm
  ```
- [ ] Wait for deployment to complete (5-10 minutes)
- [ ] Verify no errors in deployment output
- [ ] Note the stack name: `_________________________`

### 3.2 Verify Stack Creation
- [ ] Check CloudFormation console
- [ ] Verify stack status is `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- [ ] Review stack outputs:
  ```bash
  aws cloudformation describe-stacks \
    --stack-name aws-contact-sync-prod \
    --profile <PROFILE_NAME> \
    --query 'Stacks[0].Outputs'
  ```

---

## Step 4: Initialize Configuration

### 4.1 Run Init Script
- [ ] Run configuration initialization:
  ```bash
  bash scripts/init-config.sh
  ```
- [ ] Answer prompts:
  - Contact types to sync: `_________________________`
  - Excluded accounts: `_________________________`
  - Notify on success: `_________________________`
  - Notify on failure: `_________________________`
  - Notify on partial failure: `_________________________`
  - SNS email: `_________________________`

### 4.2 Verify Configuration
- [ ] Check DynamoDB config table:
  ```bash
  aws dynamodb get-item \
    --table-name aws-contact-sync-prod-config \
    --key '{"config_key":{"S":"default"}}' \
    --profile <PROFILE_NAME>
  ```
- [ ] Verify configuration looks correct

---

## Step 5: Configure Contacts

### 5.1 Set Security Alternate Contact
- [ ] Set Security alternate contact in management account:
  ```bash
  aws account put-alternate-contact \
    --alternate-contact-type SECURITY \
    --email-address <SECURITY_EMAIL> \
    --name "Security Team" \
    --phone-number "+1-555-0123" \
    --title "Security Contact" \
    --profile <PROFILE_NAME>
  ```
- [ ] Verify contact was set:
  ```bash
  aws account get-alternate-contact \
    --alternate-contact-type SECURITY \
    --profile <PROFILE_NAME>
  ```

### 5.2 Confirm SNS Subscription (if configured)
- [ ] Check email for SNS subscription confirmation
- [ ] Click confirmation link in email
- [ ] Verify subscription is confirmed in SNS console

---

## Step 6: Test the Deployment

### 6.1 Trigger a Test Sync
- [ ] Update a contact to trigger sync:
  ```bash
  aws account put-contact-information \
    --contact-information \
      FullName="Test User" \
      AddressLine1="123 Test St" \
      City="Test City" \
      StateOrRegion="CA" \
      PostalCode="12345" \
      CountryCode="US" \
      PhoneNumber="+1-555-0123" \
    --profile <PROFILE_NAME>
  ```

### 6.2 Monitor Execution
- [ ] Watch CloudWatch logs:
  ```bash
  aws logs tail /aws/lambda/aws-contact-sync-prod-contact-sync-handler \
    --follow \
    --profile <PROFILE_NAME>
  ```
- [ ] Verify sync operation started
- [ ] Check for any errors in logs

### 6.3 Verify Sync Results
- [ ] Check state table for sync operation:
  ```bash
  aws dynamodb scan \
    --table-name aws-contact-sync-prod-state \
    --profile <PROFILE_NAME> \
    --max-items 5
  ```
- [ ] Verify sync status is "completed"
- [ ] Check member accounts to confirm contacts were updated

### 6.4 Verify Notifications
- [ ] Check Security contact email for notification
- [ ] Check SNS email (if configured)
- [ ] Verify notification contains correct information
- [ ] Check CloudWatch logs for notification delivery status

---

## Step 7: Post-Deployment Verification

### 7.1 CloudWatch Dashboard
- [ ] Navigate to CloudWatch console
- [ ] Find dashboard: `aws-contact-sync-prod-dashboard`
- [ ] Verify metrics are being collected
- [ ] Review any alarms

### 7.2 CloudTrail Integration
- [ ] Verify CloudTrail is capturing contact change events
- [ ] Check EventBridge rules are active:
  ```bash
  aws events list-rules \
    --name-prefix aws-contact-sync \
    --profile <PROFILE_NAME>
  ```
- [ ] Verify rules are enabled

### 7.3 DynamoDB Tables
- [ ] Verify config table exists and has data
- [ ] Verify state table exists
- [ ] Check table streams are enabled on state table
- [ ] Verify TTL is configured on state table

---

## Step 8: Documentation and Handoff

### 8.1 Document Deployment
- [ ] Record all account IDs and configuration
- [ ] Document any customizations made
- [ ] Note any excluded accounts and why
- [ ] Save deployment date and version

### 8.2 Team Notification
- [ ] Notify team that system is deployed
- [ ] Share Security contact email address
- [ ] Provide CloudWatch dashboard link
- [ ] Share troubleshooting documentation

### 8.3 Monitoring Setup
- [ ] Set up CloudWatch alarms for failures
- [ ] Configure SNS notifications for alarms
- [ ] Document escalation procedures
- [ ] Schedule regular review of sync operations

---

## Troubleshooting

### Common Issues

#### Deployment Fails
- [ ] Check AWS credentials are valid
- [ ] Verify IAM permissions are sufficient
- [ ] Review CloudFormation events for errors
- [ ] Check SAM CLI version is up to date

#### SES Emails Not Sending
- [ ] Verify SES domain is verified
- [ ] Check SES is out of sandbox (or recipient is verified)
- [ ] Verify IAM role trust policy is correct
- [ ] Check Lambda has permission to assume SES role
- [ ] Review CloudWatch logs for SES errors

#### Sync Not Triggering
- [ ] Verify CloudTrail is enabled
- [ ] Check EventBridge rules are active
- [ ] Verify Lambda has Organizations permissions
- [ ] Check CloudWatch logs for errors

#### No Notifications Received
- [ ] Verify Security contact is configured
- [ ] Check SNS subscription is confirmed
- [ ] Review notification settings in config
- [ ] Check CloudWatch logs for notification errors
- [ ] Verify SES role permissions

---

## Rollback Procedure

If deployment fails or issues arise:

### Option 1: Rollback via Script
```bash
bash scripts/deploy.sh --rollback --profile <PROFILE_NAME>
```

### Option 2: Manual Rollback
- [ ] Navigate to CloudFormation console
- [ ] Select the stack
- [ ] Choose "Stack actions" → "Roll back"
- [ ] Confirm rollback

### Option 3: Delete Stack
```bash
aws cloudformation delete-stack \
  --stack-name aws-contact-sync-prod \
  --profile <PROFILE_NAME>
```

---

## Deployment Summary

### Deployment Details
- **Deployment Date**: `_________________________`
- **Deployed By**: `_________________________`
- **Stack Name**: `_________________________`
- **Region**: `_________________________`
- **Version/Commit**: `_________________________`

### Resources Created
- [ ] Lambda Functions (3)
- [ ] DynamoDB Tables (2)
- [ ] SNS Topic (1)
- [ ] EventBridge Rules (4)
- [ ] IAM Roles (3)
- [ ] CloudWatch Log Groups (3)
- [ ] CloudWatch Dashboard (1)

### Configuration Summary
- **Contact Types Synced**: `_________________________`
- **Excluded Accounts**: `_________________________`
- **Notification Settings**: `_________________________`
- **Security Contact**: `_________________________`

### Sign-Off
- [ ] Deployment completed successfully
- [ ] All tests passed
- [ ] Documentation updated
- [ ] Team notified

**Deployed By**: `_________________________`  
**Date**: `_________________________`  
**Signature**: `_________________________`

---

## Additional Resources

- **Main Documentation**: `DEPLOYMENT.md`
- **SES Setup Guide**: `SES_SETUP.md`
- **Testing Guide**: `TESTING.md`
- **GitHub Repository**: https://github.com/dsteinfeld/aws-contact-sync
- **CloudWatch Dashboard**: `https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=aws-contact-sync-prod-dashboard`

---

## Support Contacts

- **Technical Lead**: `_________________________`
- **AWS Account Owner**: `_________________________`
- **Security Team**: `_________________________`
- **On-Call Contact**: `_________________________`
