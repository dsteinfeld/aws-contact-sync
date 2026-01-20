# AWS Contact Synchronization System - Deployment Guide

This guide provides comprehensive instructions for deploying the AWS Contact Synchronization System using AWS SAM (Serverless Application Model).

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Environment-Specific Deployment](#environment-specific-deployment)
4. [Configuration](#configuration)
5. [Validation](#validation)
6. [Monitoring](#monitoring)
7. [Troubleshooting](#troubleshooting)
8. [Rollback Procedures](#rollback-procedures)

## Prerequisites

### Required Tools

- **AWS CLI v2.0+**: [Installation Guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html)
- **SAM CLI v1.50+**: [Installation Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html)
- **Python 3.9+**: Required for local development and testing
- **Git**: For version control

### AWS Permissions

The deployment user/role must have the following permissions:

- CloudFormation: Full access for stack management
- Lambda: Create and manage functions
- DynamoDB: Create and manage tables
- EventBridge: Create and manage rules
- SNS: Create and manage topics
- S3: Create and manage buckets (for CloudTrail)
- CloudTrail: Create and manage trails
- IAM: Create and manage roles and policies
- CloudWatch: Create and manage alarms and dashboards

### AWS Account Setup

1. **Organization Management Account**: The deployment must be performed from the AWS Organization's management account
2. **CloudTrail**: Ensure CloudTrail is enabled for Account Management API events
3. **Regions**: Choose a region that supports all required services (recommended: us-east-1, us-west-2, eu-west-1)

## Quick Start

### 1. Clone and Prepare

```bash
git clone <repository-url>
cd aws-contact-sync
```

**Note:** On Linux/macOS, if scripts don't have execute permissions, use `bash scripts/scriptname.sh` instead of `./scripts/scriptname.sh`

### 2. Configure AWS Credentials

```bash
aws configure
# or use a named profile
aws configure --profile my-aws-profile
# or
export AWS_PROFILE=your-profile-name
```

### 3. Deploy to Production

```bash
bash scripts/deploy.sh \
  --environment prod \
  --account-id 123456789012 \
  --notification-email admin@yourcompany.com \
  --region us-east-1

# Or with a specific AWS profile
bash scripts/deploy.sh \
  --environment prod \
  --account-id 123456789012 \
  --notification-email admin@yourcompany.com \
  --region us-east-1 \
  --profile my-aws-profile
```

### 4. Validate Deployment

```bash
bash scripts/validate.sh --environment prod

# Or with a specific AWS profile
bash scripts/validate.sh --environment prod --profile my-aws-profile
```

## Environment-Specific Deployment

### Development Environment

```bash
./scripts/deploy.sh \
  --environment dev \
  --account-id 123456789012 \
  --region us-east-1 \
  --notification-email dev-team@yourcompany.com

# Or with a specific AWS profile
./scripts/deploy.sh \
  --environment dev \
  --account-id 123456789012 \
  --region us-east-1 \
  --notification-email dev-team@yourcompany.com \
  --profile my-aws-profile
```

**Development Features:**
- Reduced resource limits
- Debug logging enabled
- X-Ray tracing enabled
- Success notifications enabled
- 7-day log retention

### Staging Environment

```bash
./scripts/deploy.sh \
  --environment staging \
  --account-id 123456789012 \
  --region us-east-1 \
  --notification-email staging-alerts@yourcompany.com
```

**Staging Features:**
- Moderate resource limits
- Performance monitoring
- 30-day log retention
- Production-like configuration

### Production Environment

```bash
./scripts/deploy.sh \
  --environment prod \
  --account-id 123456789012 \
  --region us-east-1 \
  --notification-email prod-alerts@yourcompany.com
```

**Production Features:**
- High resource limits
- Strict error thresholds
- 90-day log retention
- Automated backups
- Termination protection

## Configuration

### Environment Variables

The system uses the following environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `MANAGEMENT_ACCOUNT_ID` | AWS Organization Management Account ID | Required |
| `CONFIG_TABLE_NAME` | DynamoDB configuration table name | Auto-generated |
| `STATE_TABLE_NAME` | DynamoDB state tracking table name | Auto-generated |
| `ENVIRONMENT` | Deployment environment | prod |
| `LOG_LEVEL` | Logging level | INFO |

### System Configuration

After deployment, configure the system through the DynamoDB configuration table:

```json
{
  "config_key": "default",
  "contact_types": ["primary", "billing", "operations", "security"],
  "excluded_accounts": [],
  "retry_config": {
    "max_attempts": 3,
    "base_delay": 2,
    "max_delay": 60
  },
  "notification_settings": {
    "notify_on_failure": true,
    "notify_on_success": false,
    "notify_on_partial_failure": true,
    "failure_threshold": 1
  }
}
```

### Contact Type Configuration

Configure which contact types to synchronize:

- `primary`: Primary contact information
- `billing`: Billing contact
- `operations`: Operations contact  
- `security`: Security contact

### Account Exclusions

Add account IDs to exclude from synchronization:

```json
{
  "excluded_accounts": ["111111111111", "222222222222"]
}
```

## Validation

### Basic Validation

```bash
./scripts/validate.sh --environment prod

# Or with a specific AWS profile
./scripts/validate.sh --environment prod --profile my-aws-profile
```

Validates:
- Stack existence and health
- Lambda function status
- DynamoDB table status
- EventBridge rules
- SNS topic accessibility
- CloudWatch alarms
- System configuration

### Deep Validation

```bash
./scripts/validate.sh --environment prod --deep

# Or with a specific AWS profile
./scripts/validate.sh --environment prod --deep --profile my-aws-profile
```

Additional validation:
- Integration tests
- End-to-end workflow testing
- Performance validation

### Manual Testing

Test the system manually by:

1. **Updating contact information** in the management account
2. **Monitoring CloudWatch logs** for processing events
3. **Checking member accounts** for synchronized contacts
4. **Verifying notifications** are sent appropriately

## Monitoring

### CloudWatch Dashboard

Access the monitoring dashboard:
```
https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=aws-contact-sync-prod-dashboard
```

### Key Metrics

- **Lambda Invocations**: Number of function executions
- **Lambda Errors**: Function execution errors
- **Lambda Duration**: Function execution time
- **DynamoDB Operations**: Read/write operations
- **Dead Letter Queue**: Failed events requiring attention

### Alarms

The system creates several CloudWatch alarms:

- **Contact Sync Errors**: Triggers on any contact sync handler errors
- **Account Processor Errors**: Triggers on multiple account processor errors
- **DLQ Messages**: Triggers when messages appear in dead letter queue

### Log Analysis

View logs using CloudWatch Logs Insights:

```sql
fields @timestamp, @message
| filter @message like /ERROR/
| sort @timestamp desc
| limit 100
```

## Troubleshooting

### Common Issues

#### 1. Permission Errors

**Symptom**: Lambda functions fail with access denied errors

**Solution**:
- Verify IAM roles have correct permissions
- Check cross-account trust relationships
- Ensure Organizations API permissions

#### 2. EventBridge Not Triggering

**Symptom**: Contact changes don't trigger synchronization

**Solution**:
- Verify CloudTrail is capturing Account Management events
- Check EventBridge rule patterns
- Confirm management account ID in configuration

#### 3. DynamoDB Throttling

**Symptom**: DynamoDB throttling errors in logs

**Solution**:
- Monitor table metrics
- Consider switching to provisioned capacity
- Implement exponential backoff (already included)

#### 4. High Lambda Costs

**Symptom**: Unexpected Lambda charges

**Solution**:
- Review concurrency limits
- Optimize function memory allocation
- Check for infinite loops in processing

### Debug Mode

Enable debug logging by updating the Lambda environment variables:

```bash
aws lambda update-function-configuration \
  --function-name aws-contact-sync-prod-contact-sync-handler \
  --environment Variables='{LOG_LEVEL=DEBUG}'
```

### Log Analysis Commands

```bash
# View recent errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/aws-contact-sync-prod-contact-sync-handler \
  --filter-pattern "ERROR" \
  --start-time $(date -d '1 hour ago' +%s)000

# View sync operations
aws logs filter-log-events \
  --log-group-name /aws/lambda/aws-contact-sync-prod-contact-sync-handler \
  --filter-pattern "sync_id" \
  --start-time $(date -d '1 day ago' +%s)000
```

## Rollback Procedures

### Automatic Rollback

For failed deployments, SAM automatically rolls back:

```bash
# Check rollback status
aws cloudformation describe-stacks \
  --stack-name aws-contact-sync-prod \
  --query 'Stacks[0].StackStatus'
```

### Manual Rollback

```bash
# Rollback to previous version
./scripts/rollback.sh --environment prod

# Force rollback without confirmation
./scripts/rollback.sh --environment prod --force

# Backup data before rollback
./scripts/rollback.sh --environment prod --backup-data

# With a specific AWS profile
./scripts/rollback.sh --environment prod --profile my-aws-profile
```

### Complete Stack Deletion

```bash
# Delete stack with data backup
./scripts/rollback.sh --environment dev --delete --backup-data

# With a specific AWS profile
./scripts/rollback.sh --environment dev --delete --backup-data --profile my-aws-profile
```

### Data Recovery

If data needs to be restored after rollback:

1. **Locate backup files** (created during rollback)
2. **Recreate the stack** using deployment scripts
3. **Restore data** using AWS CLI:

```bash
# Restore configuration
aws dynamodb batch-write-item \
  --request-items file://backup/config-table-backup.json

# Restore state data
aws dynamodb batch-write-item \
  --request-items file://backup/state-table-backup.json
```

## Security Considerations

### IAM Roles

- Lambda functions use least-privilege IAM roles
- Cross-account permissions limited to necessary operations
- Regular review of permissions recommended

### Data Encryption

- DynamoDB tables encrypted at rest
- SNS topics encrypted with AWS KMS
- CloudTrail logs encrypted in S3

### Network Security

- Lambda functions run in AWS managed VPC
- No public internet access required
- All communication over HTTPS/TLS

### Compliance

- All operations logged for audit purposes
- 90-day log retention in production
- Data residency controlled by region selection

## Support and Maintenance

### Regular Maintenance

1. **Monitor CloudWatch alarms** daily
2. **Review error logs** weekly
3. **Update dependencies** monthly
4. **Test disaster recovery** quarterly

### Updates and Patches

1. **Test in development** environment first
2. **Deploy to staging** for validation
3. **Deploy to production** during maintenance window
4. **Monitor post-deployment** for issues

### Contact Information

For support and questions:
- **Technical Issues**: Check CloudWatch logs and alarms
- **Configuration Changes**: Update DynamoDB configuration table
- **Emergency Rollback**: Use rollback scripts immediately

---

## Appendix

### SAM Template Parameters

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| ManagementAccountId | String | AWS Organization Management Account ID | Required |
| Environment | String | Deployment environment | prod |
| NotificationEmail | String | Email for notifications | Optional |
| LogRetentionDays | Number | CloudWatch log retention | 90 |

### Resource Naming Convention

Resources follow the pattern: `{StackName}-{Environment}-{ResourceType}`

Examples:
- `aws-contact-sync-prod-contact-sync-handler`
- `aws-contact-sync-prod-config`
- `aws-contact-sync-prod-notifications`

### Cost Estimation

Typical monthly costs (production environment):

- **Lambda**: $10-50 (depending on organization size)
- **DynamoDB**: $5-20 (pay-per-request)
- **CloudWatch**: $5-15 (logs and metrics)
- **SNS**: $1-5 (notifications)
- **CloudTrail**: $2-10 (data events)

**Total**: $23-100/month for typical organization