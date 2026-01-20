"""Lambda handler for processing notification events."""

import json
import logging
import os
from typing import Dict, Any
from ..notifications.notification_handler import NotificationHandler
from ..config.dynamodb_config_manager import DynamoDBConfigManager
from ..models.sync_models import SyncOperation, AccountSyncResult
from ..models.contact_models import ContactInformation, AlternateContact
from datetime import datetime, timezone

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for processing notification events.
    
    Args:
        event: Lambda event containing notification request
        context: Lambda context
        
    Returns:
        Response dictionary with status and details
    """
    try:
        logger.info(f"Processing notification event: {json.dumps(event, default=str)}")
        
        # Get configuration
        config_table = os.environ.get('CONFIG_TABLE_NAME', 'contact-sync-config')
        region = os.environ.get('AWS_REGION', 'us-east-1')
        
        # Initialize components
        config_manager = DynamoDBConfigManager(config_table, region)
        notification_handler = NotificationHandler(config_manager, region)
        
        # Parse event type
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