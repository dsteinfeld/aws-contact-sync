# AWS Contact Sync Testing Guide

This document provides a comprehensive testing plan for the AWS Contact Synchronization system.

## Prerequisites

- AWS CLI configured with `orgtest` profile
- Access to management account (889662168126)
- 4 member accounts in the organization
- Security alternate contact configured in management account

## Test 1: Initialize Configuration

**Purpose**: Populate DynamoDB config table with default settings

**Steps**:
```powershell
bash scripts/init-config-simple.sh
```

**Expected Result**:
- Configuration created in `aws-contact-sync-prod-config` table
- Config includes all 4 contact types
- No excluded accounts
- Notification settings configured

**Verification**:
```powershell
aws dynamodb get-item --table-name aws-contact-sync-prod-config --key '{"config_key":{"S":"current"}}' --profile orgtest --region us-east-1
```

---

## Test 2: Basic Sync with Full Configuration

**Purpose**: Verify sync works with all contact types enabled

**Steps**:
1. Make a BILLING contact change in management account via AWS Console
2. Wait 30 seconds for EventBridge/Lambda processing
3. Check CloudWatch logs for ContactSyncHandler
4. Check CloudWatch logs for AccountProcessorHandler
5. Verify all 4 member accounts were updated

**Expected Result**:
- ContactSyncHandler log shows: "Found 4 active member accounts"
- ContactSyncHandler log shows: "Successfully initiated synchronization for 4 accounts"
- AccountProcessorHandler logs show 4 successful updates
- All 4 member accounts have the updated BILLING contact

**Verification Commands**:
```powershell
# Check ContactSyncHandler logs
aws logs tail /aws/lambda/aws-contact-sync-prod-contact-sync-handler --follow --profile orgtest --region us-east-1

# Check AccountProcessorHandler logs
aws logs tail /aws/lambda/aws-contact-sync-prod-account-processor-handler --follow --profile orgtest --region us-east-1

# Verify contact in a member account
aws account get-alternate-contact --alternate-contact-type BILLING --account-id <MEMBER_ACCOUNT_ID> --profile orgtest --region us-east-1
```

---

## Test 3: Contact Type Filtering

**Purpose**: Verify that excluded contact types don't trigger sync

**Steps**:
1. Update config to only sync BILLING:
   ```powershell
   bash scripts/test-config-filtering.sh
   # Choose option 2
   ```

2. Make an OPERATIONS contact change in management account
3. Check logs - should see "Contact type OPERATIONS is not configured for synchronization"
4. Verify NO sync operation was initiated

5. Make a BILLING contact change in management account
6. Check logs - should see sync operation for 4 accounts
7. Verify all 4 accounts were updated

**Expected Results**:
- OPERATIONS change: No sync triggered, log shows contact type not configured
- BILLING change: Sync triggered normally, all accounts updated

**Verification**:
```powershell
# Check logs for filtering message
aws logs filter-log-events \
    --log-group-name /aws/lambda/aws-contact-sync-prod-contact-sync-handler \
    --filter-pattern "not configured for synchronization" \
    --profile orgtest --region us-east-1
```

---

## Test 4: Account Exclusion

**Purpose**: Verify excluded accounts are not synced

**Steps**:
1. Choose one member account ID to exclude
2. Update config to exclude that account:
   ```powershell
   bash scripts/test-config-filtering.sh
   # Choose option 3
   # Enter the account ID
   ```

3. Make a BILLING contact change in management account
4. Check logs - should see "Account <ID> excluded by configuration"
5. Verify only 3 accounts were synced (excluded account skipped)

**Expected Results**:
- ContactSyncHandler log shows: "Account <ID> excluded by configuration"
- ContactSyncHandler log shows: "Filtered 4 accounts to 3 after applying exclusions"
- Only 3 AccountProcessorHandler invocations
- Excluded account NOT updated
- Other 3 accounts updated successfully

**Verification**:
```powershell
# Check for exclusion message
aws logs filter-log-events \
    --log-group-name /aws/lambda/aws-contact-sync-prod-contact-sync-handler \
    --filter-pattern "excluded by configuration" \
    --profile orgtest --region us-east-1

# Verify excluded account was NOT updated (contact should be old value)
aws account get-alternate-contact --alternate-contact-type BILLING --account-id <EXCLUDED_ACCOUNT_ID> --profile orgtest --region us-east-1
```

---

## Test 5: Notifications

**Purpose**: Verify notifications are sent to Security contact

**Steps**:
1. Reset config to full sync:
   ```powershell
   bash scripts/test-config-filtering.sh
   # Choose option 1
   ```

2. Make a BILLING contact change
3. Wait for sync to complete
4. Check Security contact email for notification
5. Check NotificationHandler logs

**Expected Results**:
- NotificationHandler Lambda is invoked (currently NOT implemented - needs to be added)
- Notification sent via User Notifications to Security contact email
- If User Notifications fails, SNS fallback is used
- Email received at Security contact address

**Current Status**: ⚠️ **NOT IMPLEMENTED**
- ContactSyncHandler and AccountProcessorHandler don't invoke NotificationHandler yet
- This needs to be implemented before testing

**Verification**:
```powershell
# Check NotificationHandler logs (once implemented)
aws logs tail /aws/lambda/aws-contact-sync-prod-notification-handler --follow --profile orgtest --region us-east-1
```

---

## Test 6: Primary Contact Sync

**Purpose**: Verify primary contact synchronization works correctly

**Steps**:
1. Make a primary contact change in management account
2. Check logs for sync operation
3. Verify all member accounts updated

**Expected Results**:
- Sync triggered for primary contact type
- All member accounts updated with new primary contact info
- No infinite loop (EventBridge filter working correctly)

**Known Issue**: ⚠️ Primary contact comparison may show account name instead of contact name
- This needs testing in production environment with more accounts

---

## Test 7: Error Handling and Retry

**Purpose**: Verify retry logic works for transient failures

**Steps**:
1. Temporarily remove IAM permissions from AccountProcessorHandler
2. Make a contact change
3. Check logs for retry attempts
4. Restore IAM permissions
5. Verify eventual success

**Expected Results**:
- AccountProcessorHandler logs show retry attempts
- Exponential backoff delays visible in logs
- After restoring permissions, sync succeeds

---

## Test 8: State Tracking

**Purpose**: Verify sync operations are tracked in DynamoDB

**Steps**:
1. Make a contact change
2. Query state table for sync operation record
3. Verify all account results are recorded

**Expected Results**:
- Sync operation created in `aws-contact-sync-prod-state` table
- Sync ID, timestamp, contact type recorded
- Account results show success/failure for each account

**Verification**:
```powershell
# Query recent sync operations
aws dynamodb scan \
    --table-name aws-contact-sync-prod-state \
    --profile orgtest --region us-east-1 \
    --max-items 5
```

---

## Cleanup After Testing

1. Reset configuration to production settings:
   ```powershell
   bash scripts/test-config-filtering.sh
   # Choose option 1 (full sync)
   ```

2. Verify all accounts have correct contact information

3. Document any issues found during testing

---

## Known Issues / TODO

1. **Notifications not implemented**: ContactSyncHandler and AccountProcessorHandler need to invoke NotificationHandler
2. **Primary contact comparison**: May show account name instead of contact name (needs production testing)
3. **User Notifications API**: May need adjustment based on actual AWS User Notifications service behavior

---

## Success Criteria

- ✅ Configuration filtering works (contact types)
- ✅ Account exclusion works
- ✅ Alternate contacts sync correctly
- ⚠️ Primary contacts sync correctly (needs more testing)
- ❌ Notifications sent to Security contact (not implemented)
- ✅ State tracking records sync operations
- ✅ Retry logic handles transient failures
- ✅ No infinite loops (EventBridge filter working)
