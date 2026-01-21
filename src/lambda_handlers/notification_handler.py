"""Lambda handler for processing notification events."""

import json
import logging
import os
from typing import Dict, Any, List, Set
from ..notifications.notification_handler import NotificationHandler
from ..config.dynamodb_config_manager import DynamoDBConfigManager
from ..config.dynamodb_state_tracker import DynamoDBStateTracker
from ..models.sync_models import SyncOperation, AccountSyncResult
from ..models.contact_models import ContactInformation, AlternateContact
from datetime import datetime, timezone

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for processing notification events.
    
    This handler supports two event types:
    1. DynamoDB Stream events - triggered when sync operation state changes
    2. Direct invocation events - for testing and manual notifications
    
    Args:
        event: Lambda event containing notification request or DynamoDB Stream records
        context: Lambda context
        
    Returns:
        Response dictionary with status and details
    """
    try:
        logger.info(f"Processing notification event: {json.dumps(event, default=str)}")
        
        # Get configuration
        config_table = os.environ.get('CONFIG_TABLE_NAME', 'contact-sync-config')
        state_table = os.environ.get('STATE_TABLE_NAME', 'aws-contact-sync-state')
        region = os.environ.get('AWS_REGION', 'us-east-1')
        
        # Initialize components
        config_manager = DynamoDBConfigManager(config_table, region)
        notification_handler = NotificationHandler(config_manager, region)
        state_tracker = DynamoDBStateTracker(state_table, region)
        
        # Check if this is a DynamoDB Stream event
        if 'Records' in event and event['Records'] and 'dynamodb' in event['Records'][0]:
            return handle_dynamodb_stream(notification_handler, state_tracker, event)
        
        # Otherwise, handle as direct invocation
        event_type = event.get('notification_type', 'sync_completion')
        
        if event_type == 'sync_completion':
            return handle_sync_completion(notification_handler, event)
        elif event_type == 'system_error':
            return handle_system_error(notification_handler, event)
        elif event_type == 'configuration_error':
            return handle_configuration_error(notification_handler, event)
        elif event_type == 'test_delivery':
            return handle_test_delivery(notification_handler, event)
        else:
            logger.error(f"Unknown notification type: {event_type}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Unknown notification type: {event_type}'
                })
            }
            
    except Exception as e:
        logger.error(f"Error processing notification event: {e}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal server error',
                'details': str(e)
            })
        }


def handle_dynamodb_stream(
    notification_handler: NotificationHandler,
    state_tracker: DynamoDBStateTracker,
    event: Dict[str, Any]
) -> Dict[str, Any]:
    """Handle DynamoDB Stream events to detect completed sync operations.
    
    This function processes stream records to identify when all accounts in a sync
    operation have completed (success or failure), then sends an aggregated notification.
    
    Args:
        notification_handler: Notification handler instance
        state_tracker: State tracker for querying sync operations
        event: DynamoDB Stream event with records
        
    Returns:
        Response dictionary with processing results
    """
    try:
        records = event.get('Records', [])
        logger.info(f"Processing {len(records)} DynamoDB Stream records")
        
        # Track unique sync_ids that may be complete
        sync_ids_to_check: Set[str] = set()
        
        # Process each stream record
        for record in records:
            event_name = record.get('eventName')
            
            # We only care about MODIFY events (status updates)
            if event_name != 'MODIFY':
                continue
            
            # Extract sync_id from the record
            dynamodb_data = record.get('dynamodb', {})
            new_image = dynamodb_data.get('NewImage', {})
            
            if 'sync_id' not in new_image:
                logger.warning("Stream record missing sync_id, skipping")
                continue
            
            sync_id = new_image['sync_id'].get('S', '')
            if not sync_id:
                continue
            
            logger.info(f"Detected state change for sync operation {sync_id}")
            sync_ids_to_check.add(sync_id)
        
        # Check each sync operation to see if it's complete
        notifications_sent = 0
        for sync_id in sync_ids_to_check:
            if check_and_notify_if_complete(notification_handler, state_tracker, sync_id):
                notifications_sent += 1
        
        logger.info(f"Processed {len(sync_ids_to_check)} sync operations, sent {notifications_sent} notifications")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'records_processed': len(records),
                'sync_operations_checked': len(sync_ids_to_check),
                'notifications_sent': notifications_sent
            })
        }
        
    except Exception as e:
        logger.error(f"Error handling DynamoDB Stream: {e}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to process DynamoDB Stream',
                'details': str(e)
            })
        }


def check_and_notify_if_complete(
    notification_handler: NotificationHandler,
    state_tracker: DynamoDBStateTracker,
    sync_id: str
) -> bool:
    """Check if a sync operation is complete and send notification if so.
    
    A sync operation is considered complete when all target accounts have
    a result with status != "pending".
    
    Args:
        notification_handler: Notification handler instance
        state_tracker: State tracker for querying sync operations
        sync_id: Sync operation ID to check
        
    Returns:
        True if notification was sent, False otherwise
    """
    try:
        # Add a small delay to allow DynamoDB to become consistent
        # This helps ensure we read the latest state after rapid updates
        import time
        time.sleep(0.5)
        
        # Retrieve the full sync operation
        sync_operation = state_tracker.get_sync_operation(sync_id)
        
        if not sync_operation:
            logger.warning(f"Sync operation {sync_id} not found")
            return False
        
        # Check if all accounts have completed
        target_accounts = set(sync_operation.target_accounts)
        completed_accounts = set()
        pending_accounts = []
        
        for account_id in target_accounts:
            result = sync_operation.results.get(account_id)
            
            if result and result.status != "pending":
                completed_accounts.add(account_id)
            else:
                pending_accounts.append(account_id)
        
        # If there are still pending accounts, don't send notification yet
        if pending_accounts:
            logger.info(
                f"Sync operation {sync_id} not yet complete: "
                f"{len(completed_accounts)}/{len(target_accounts)} accounts finished, "
                f"waiting on: {pending_accounts}"
            )
            return False
        
        # All accounts are complete - send notification
        logger.info(
            f"Sync operation {sync_id} is complete: "
            f"{len(completed_accounts)}/{len(target_accounts)} accounts finished"
        )
        
        # Update overall sync status to completed
        state_tracker.update_sync_status(sync_id, "completed")
        
        # Send aggregated notification
        success = notification_handler.handle_sync_completion(sync_operation)
        
        if success:
            logger.info(f"Successfully sent completion notification for sync {sync_id}")
        else:
            logger.warning(f"Failed to send completion notification for sync {sync_id}")
        
        return success
        
    except Exception as e:
        logger.error(f"Error checking sync completion for {sync_id}: {e}", exc_info=True)
        return False


def handle_sync_completion(notification_handler: NotificationHandler, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle sync completion notification.
    
    Args:
        notification_handler: Notification handler instance
        event: Event containing sync operation data
        
    Returns:
        Response dictionary
    """
    try:
        # Parse sync operation from event
        sync_operation = parse_sync_operation(event.get('sync_operation', {}))
        
        # Send notification
        success = notification_handler.handle_sync_completion(sync_operation)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': success,
                'sync_id': sync_operation.sync_id,
                'notification_sent': success
            })
        }
        
    except Exception as e:
        logger.error(f"Error handling sync completion notification: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to process sync completion notification',
                'details': str(e)
            })
        }


def handle_system_error(notification_handler: NotificationHandler, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle system error notification.
    
    Args:
        notification_handler: Notification handler instance
        event: Event containing system error data
        
    Returns:
        Response dictionary
    """
    try:
        # Parse sync operation and error message
        sync_operation = parse_sync_operation(event.get('sync_operation', {}))
        error_message = event.get('error_message', 'Unknown system error')
        
        # Send notification
        success = notification_handler.handle_system_error(sync_operation, error_message)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': success,
                'sync_id': sync_operation.sync_id,
                'notification_sent': success
            })
        }
        
    except Exception as e:
        logger.error(f"Error handling system error notification: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to process system error notification',
                'details': str(e)
            })
        }


def handle_configuration_error(notification_handler: NotificationHandler, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle configuration error notification.
    
    Args:
        notification_handler: Notification handler instance
        event: Event containing configuration error data
        
    Returns:
        Response dictionary
    """
    try:
        error_message = event.get('error_message', 'Unknown configuration error')
        config_details = event.get('config_details', {})
        
        # Send notification
        success = notification_handler.handle_configuration_error(error_message, config_details)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': success,
                'notification_sent': success
            })
        }
        
    except Exception as e:
        logger.error(f"Error handling configuration error notification: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to process configuration error notification',
                'details': str(e)
            })
        }


def handle_test_delivery(notification_handler: NotificationHandler, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle test delivery notification.
    
    Args:
        notification_handler: Notification handler instance
        event: Event containing test delivery request
        
    Returns:
        Response dictionary
    """
    try:
        # Test notification delivery
        results = notification_handler.test_notification_delivery()
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'test_results': results
            })
        }
        
    except Exception as e:
        logger.error(f"Error handling test delivery: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Failed to test notification delivery',
                'details': str(e)
            })
        }


def parse_sync_operation(sync_data: Dict[str, Any]) -> SyncOperation:
    """Parse sync operation from event data.
    
    Args:
        sync_data: Dictionary containing sync operation data
        
    Returns:
        SyncOperation instance
    """
    # Parse contact data
    contact_data_dict = sync_data.get('contact_data', {})
    contact_type = sync_data.get('contact_type', 'primary')
    
    if contact_type == 'primary':
        contact_data = ContactInformation(**contact_data_dict)
    else:
        contact_data = AlternateContact(**contact_data_dict)
    
    # Parse results
    results = {}
    for account_id, result_data in sync_data.get('results', {}).items():
        # Parse timestamp
        timestamp_str = result_data.get('timestamp')
        if isinstance(timestamp_str, str):
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            timestamp = datetime.now(timezone.utc)
        
        results[account_id] = AccountSyncResult(
            account_id=account_id,
            status=result_data.get('status', 'failed'),
            timestamp=timestamp,
            error_message=result_data.get('error_message'),
            retry_count=result_data.get('retry_count', 0)
        )
    
    # Parse main timestamp
    timestamp_str = sync_data.get('timestamp')
    if isinstance(timestamp_str, str):
        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    else:
        timestamp = datetime.now(timezone.utc)
    
    return SyncOperation(
        sync_id=sync_data.get('sync_id', 'unknown'),
        timestamp=timestamp,
        initiating_user=sync_data.get('initiating_user', 'unknown'),
        contact_type=contact_type,
        source_account=sync_data.get('source_account', 'unknown'),
        target_accounts=sync_data.get('target_accounts', []),
        status=sync_data.get('status', 'completed'),
        contact_data=contact_data,
        results=results
    )