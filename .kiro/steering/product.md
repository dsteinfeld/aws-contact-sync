# Product Overview

## AWS Contact Synchronization System

A serverless Python application that automatically synchronizes contact information from an AWS organization's management account to all member accounts. When contacts or alternate contacts are modified in the management account, those changes are propagated to ensure consistent contact information across the entire organization.

## Key Features

- **Event-driven synchronization**: Uses CloudTrail events via EventBridge to detect contact changes in near real-time
- **Comprehensive contact support**: Handles both primary contact information and alternate contacts (billing, operations, security)
- **Resilient processing**: Implements retry logic with exponential backoff for failed updates
- **Configurable filtering**: Supports exclusion lists and selective contact type synchronization
- **Audit compliance**: Maintains detailed logs of all synchronization activities for 90+ days
- **Multi-channel notifications**: Uses AWS User Notifications with fallback to SNS for status updates

## Target Use Case

Organizations with multiple AWS accounts that need to maintain consistent contact information across all accounts for compliance, billing, and operational purposes. Eliminates manual contact updates and ensures organization-wide consistency.