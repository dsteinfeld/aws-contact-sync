"""
Contact Sync Handler Lambda Function

Main orchestrator function triggered by EventBridge that:
- Parses incoming contact change events from CloudTrail
- Retrieves organization member accounts
- Applies configuration-based filtering
- Initiates synchronization to member accounts
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

import boto3
from botocore.exceptions import ClientError

from ..events.cloudtrail_parser import CloudTrailEventParser, ContactChangeEvent
from ..aws_clients.organizations import OrganizationsClient
from ..config.dynamodb_config_manager import DynamoDBConfigManager
from ..config.dynamodb_state_tracker import DynamoDBStateTracker
from ..models.sync_models import SyncOperation, AccountSyncResult

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ContactSyncHandler:
    """Main orchestrator for contact synchronization operations."""
    
    def __init__(self, 
                 management_account_id: str,
                 config_table_name: Optional[str] = None,
                 state_table_name: Optional[str] = None,
                 account_processor_function_name: Optional[str] = None):
        """Initialize the contact sync handler.
        
        Args:
            management_account_id: AWS management account ID for filtering
            config_table_name: DynamoDB table name for configuration (optional)
            state_table_name: DynamoDB table name for state tracking (optional)
            account_processor_function_name: Lambda function name for processing individual accounts
        """
        self.management_account_id = management_account_id
        
        # Initialize components
        self.event_parser = CloudTrailEventParser(management_account_id)
        self.organizations_client = OrganizationsClient()
        
        # Initialize configuration and state managers
        self.config_manager = DynamoDBConfigManager(
            table_name=config_table_name or os.environ.get('CONFIG_TABLE_NAME', 'aws-contact-sync-config')
        )
        self.state_tracker = DynamoDBStateTracker(
            table_name=state_table_name or os.environ.get('STATE_TABLE_NAME', 'aws-contact-sync-state')
        )
        
        # Lambda client for invoking account processor
        self.lambda_client = boto3.client('lambda')
        self.account_processor_function = (
            account_processor_function_name or 
            os.environ.get('ACCOUNT_PROCESSOR_FUNCTION_NAME', 'aws-contact-sync-account-processor')
        )
    
    def handle_lambda_event(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """
        Main Lambda handler function.
        
        Args:
            event: Lambda event containing EventBridge records
            context: Lambda context object
            
        Returns:
            Dict containing processing results
        """
        try:
            logger.info(f"Processing Lambda event with {len(event.get('Records', []))} records")
            
            # Parse contact change events from the Lambda event
            contact_events = self.event_parser.parse_lambda_event(event)
            
            if not contact_events:
                logger.info("No valid contact change events found in Lambda event")
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'message': 'No contact change events to process',
                        'processed_events': 0
                    })
                }
            
            # Process each contact change event
            results = []
            for contact_event in contact_events:
                try:
                    result = self.process_contact_change(contact_event)
                    results.append(result)
                except Exception as e:
                    logger.error(f"Failed to process contact event {contact_event.event_id}: {e}")
                    results.append({
                        'event_id': contact_event.event_id,
                        'status': 'failed',
                        'error': str(e)
                    })
            
            logger.info(f"Completed processing {len(results)} contact change events")
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Processed {len(results)} contact change events',
                    'results': results
                })
            }
            
        except Exception as e:
            logger.error(f"Unexpected error in Lambda handler: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Internal server error',
                    'message': str(e)
                })
            }
    
    def process_contact_change(self, contact_event: ContactChangeEvent) -> Dict[str, Any]:
        """
        Process a single contact change event.
        
        Args:
            contact_event: Parsed contact change event
            
        Returns:
            Dict containing processing results
        """
        logger.info(f"Processing contact change event {contact_event.event_id} for {contact_event.contact_type}")
        
        try:
            # Check if this contact type should be synchronized
            if not self.should_sync_contact_type(contact_event.contact_type):
                logger.info(f"Contact type {contact_event.contact_type} is not configured for synchronization")
                return {
                    'event_id': contact_event.event_id,
                    'status': 'skipped',
                    'reason': f'Contact type {contact_event.contact_type} not configured for sync'
                }
            
            # Get organization member accounts
            member_accounts = self.get_target_accounts()
            
            if not member_accounts:
                logger.warning("No member accounts found for synchronization")
                return {
                    'event_id': contact_event.event_id,
                    'status': 'completed',
                    'message': 'No member accounts to synchronize'
                }
            
            # Apply configuration-based filtering
            filtered_accounts = self.filter_target_accounts(member_accounts)
            
            if not filtered_accounts:
                logger.info("All member accounts filtered out by configuration")
                return {
                    'event_id': contact_event.event_id,
                    'status': 'completed',
                    'message': 'All accounts filtered out by configuration'
                }
            
            # Create sync operation
            sync_operation = self.create_sync_operation(contact_event, filtered_accounts)
            
            # Start synchronization process
            self.initiate_account_synchronization(sync_operation)
            
            logger.info(f"Successfully initiated synchronization for {len(filtered_accounts)} accounts")
            
            return {
                'event_id': contact_event.event_id,
                'sync_id': sync_operation.sync_id,
                'status': 'initiated',
                'target_accounts': len(filtered_accounts),
                'message': f'Synchronization initiated for {len(filtered_accounts)} member accounts'
            }
            
        except Exception as e:
            logger.error(f"Error processing contact change event {contact_event.event_id}: {e}")
            raise
    
    def should_sync_contact_type(self, contact_type: str) -> bool:
        """
        Check if a contact type should be synchronized based on configuration.
        
        Args:
            contact_type: Contact type to check
            
        Returns:
            bool: True if contact type should be synchronized
        """
        try:
            return self.config_manager.should_sync_contact_type(contact_type)
        except Exception as e:
            logger.warning(f"Error checking contact type configuration: {e}")
            # Default to sync all contact types if configuration is unavailable
            return True
    
    def get_target_accounts(self) -> List[str]:
        """
        Get list of member account IDs for synchronization.
        
        Returns:
            List of member account IDs
        """
        try:
            member_accounts = self.organizations_client.list_active_member_accounts(
                exclude_management_account=True
            )
            account_ids = [account.account_id for account in member_accounts]
            
            logger.info(f"Found {len(account_ids)} active member accounts")
            return account_ids
            
        except Exception as e:
            logger.error(f"Failed to retrieve member accounts: {e}")
            raise
    
    def filter_target_accounts(self, account_ids: List[str]) -> List[str]:
        """
        Filter account IDs based on configuration exclusions.
        
        Args:
            account_ids: List of account IDs to filter
            
        Returns:
            List of account IDs after applying filters
        """
        try:
            filtered_accounts = []
            
            for account_id in account_ids:
                if not self.config_manager.is_account_excluded(account_id):
                    filtered_accounts.append(account_id)
                else:
                    logger.info(f"Account {account_id} excluded by configuration")
            
            logger.info(f"Filtered {len(account_ids)} accounts to {len(filtered_accounts)} after applying exclusions")
            return filtered_accounts
            
        except Exception as e:
            logger.warning(f"Error applying account filters: {e}")
            # Return all accounts if filtering fails
            return account_ids
    
    def create_sync_operation(self, contact_event: ContactChangeEvent, target_accounts: List[str]) -> SyncOperation:
        """
        Create a sync operation record.
        
        Args:
            contact_event: Contact change event
            target_accounts: List of target account IDs
            
        Returns:
            SyncOperation object
        """
        sync_id = str(uuid.uuid4())
        
        # Initialize results for all target accounts
        results = {}
        for account_id in target_accounts:
            results[account_id] = AccountSyncResult(
                account_id=account_id,
                status="pending",
                timestamp=datetime.utcnow()
            )
        
        sync_operation = SyncOperation(
            sync_id=sync_id,
            timestamp=contact_event.event_time,
            initiating_user=contact_event.initiating_user,
            contact_type=contact_event.contact_type,
            source_account=contact_event.source_account,
            target_accounts=target_accounts,
            status="pending",
            contact_data=contact_event.contact_data,
            results=results
        )
        
        # Store the sync operation
        try:
            created_sync_op = self.state_tracker.create_sync_operation(
                initiating_user=sync_operation.initiating_user,
                contact_type=sync_operation.contact_type,
                source_account=sync_operation.source_account,
                target_accounts=sync_operation.target_accounts,
                contact_data=sync_operation.contact_data
            )
            logger.info(f"Created sync operation {created_sync_op.sync_id}")
            return created_sync_op
        except Exception as e:
            logger.error(f"Failed to create sync operation record: {e}")
            # Continue with synchronization even if state tracking fails
            return sync_operation
    
    def initiate_account_synchronization(self, sync_operation: SyncOperation) -> None:
        """
        Initiate synchronization for all target accounts.
        
        Args:
            sync_operation: Sync operation containing target accounts and contact data
        """
        logger.info(f"Initiating synchronization for {len(sync_operation.target_accounts)} accounts")
        
        # Update sync operation status to in_progress
        try:
            sync_operation.status = "in_progress"
            self.state_tracker.update_sync_status(sync_operation.sync_id, "in_progress")
        except Exception as e:
            logger.warning(f"Failed to update sync operation status: {e}")
        
        # Invoke account processor Lambda for each target account
        successful_invocations = 0
        failed_invocations = 0
        
        for account_id in sync_operation.target_accounts:
            try:
                self.invoke_account_processor(sync_operation, account_id)
                successful_invocations += 1
            except Exception as e:
                logger.error(f"Failed to invoke account processor for {account_id}: {e}")
                failed_invocations += 1
                
                # Update the account result to failed
                try:
                    sync_operation.results[account_id].status = "failed"
                    sync_operation.results[account_id].error_message = str(e)
                    sync_operation.results[account_id].timestamp = datetime.utcnow()
                except Exception:
                    pass  # Continue processing other accounts
        
        logger.info(f"Account processor invocations: {successful_invocations} successful, {failed_invocations} failed")
        
        # If all invocations failed, mark the sync operation as failed
        if failed_invocations > 0 and successful_invocations == 0:
            try:
                sync_operation.status = "failed"
                self.state_tracker.update_sync_status(sync_operation.sync_id, "failed")
            except Exception as e:
                logger.error(f"Failed to update sync operation status to failed: {e}")
    
    def invoke_account_processor(self, sync_operation: SyncOperation, account_id: str) -> None:
        """
        Invoke the account processor Lambda function for a specific account.
        
        Args:
            sync_operation: Sync operation containing contact data
            account_id: Target account ID to process
        """
        payload = {
            'sync_id': sync_operation.sync_id,
            'account_id': account_id,
            'contact_type': sync_operation.contact_type,
            'contact_data': self.serialize_contact_data(sync_operation.contact_data),
            'initiating_user': sync_operation.initiating_user
        }
        
        try:
            response = self.lambda_client.invoke(
                FunctionName=self.account_processor_function,
                InvocationType='Event',  # Asynchronous invocation
                Payload=json.dumps(payload)
            )
            
            logger.debug(f"Successfully invoked account processor for {account_id}")
            
        except ClientError as e:
            logger.error(f"Failed to invoke account processor for {account_id}: {e}")
            raise
    
    def serialize_contact_data(self, contact_data) -> Dict[str, Any]:
        """
        Serialize contact data for Lambda payload.
        
        Args:
            contact_data: ContactInformation or AlternateContact object
            
        Returns:
            Dict representation of contact data
        """
        if hasattr(contact_data, '__dict__'):
            return contact_data.__dict__
        else:
            # Fallback for dataclass serialization
            from dataclasses import asdict
            return asdict(contact_data)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point for contact sync handler.
    
    Args:
        event: Lambda event from EventBridge
        context: Lambda context object
        
    Returns:
        Dict containing processing results
    """
    # Get management account ID from environment
    management_account_id = os.environ.get('MANAGEMENT_ACCOUNT_ID')
    if not management_account_id:
        logger.error("MANAGEMENT_ACCOUNT_ID environment variable not set")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Configuration error',
                'message': 'MANAGEMENT_ACCOUNT_ID environment variable not set'
            })
        }
    
    # Initialize and run the handler
    handler = ContactSyncHandler(management_account_id)
    return handler.handle_lambda_event(event, context)