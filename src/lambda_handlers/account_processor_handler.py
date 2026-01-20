"""
Account Processor Lambda Function

Processes individual member account contact updates with:
- Contact information comparison and change detection
- Retry logic with exponential backoff
- Comprehensive error handling and logging
- State tracking integration
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union

import boto3
from botocore.exceptions import ClientError, BotoCoreError

from ..aws_clients.account_management import AccountManagementClient
from ..aws_clients.organizations import OrganizationsClient
from ..config.dynamodb_state_tracker import DynamoDBStateTracker
from ..models.contact_models import ContactInformation, AlternateContact
from ..models.sync_models import AccountSyncResult

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class AccountProcessorHandler:
    """Handles contact updates for individual member accounts."""
    
    def __init__(self, 
                 management_account_id: str,
                 state_table_name: Optional[str] = None,
                 max_retry_attempts: int = 3,
                 base_retry_delay: float = 2.0,
                 max_retry_delay: float = 60.0):
        """Initialize the account processor handler.
        
        Args:
            management_account_id: AWS management account ID
            state_table_name: DynamoDB table name for state tracking (optional)
            max_retry_attempts: Maximum number of retry attempts
            base_retry_delay: Base delay for exponential backoff (seconds)
            max_retry_delay: Maximum delay for exponential backoff (seconds)
        """
        self.management_account_id = management_account_id
        self.max_retry_attempts = max_retry_attempts
        self.base_retry_delay = base_retry_delay
        self.max_retry_delay = max_retry_delay
        
        # Initialize AWS clients
        self.account_mgmt_client = AccountManagementClient()
        self.organizations_client = OrganizationsClient()
        
        # Initialize state tracker
        self.state_tracker = DynamoDBStateTracker(
            table_name=state_table_name or os.environ.get('STATE_TABLE_NAME', 'aws-contact-sync-state')
        )
    
    def handle_lambda_event(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """
        Main Lambda handler function.
        
        Args:
            event: Lambda event containing sync operation details
            context: Lambda context object
            
        Returns:
            Dict containing processing results
        """
        try:
            logger.info(f"Processing account update event: {json.dumps(event, default=str)}")
            
            # Extract required parameters from event
            sync_id = event.get('sync_id')
            account_id = event.get('account_id')
            contact_type = event.get('contact_type')
            contact_data = event.get('contact_data')
            initiating_user = event.get('initiating_user')
            
            # Validate required parameters
            if not all([sync_id, account_id, contact_type, contact_data]):
                error_msg = "Missing required parameters: sync_id, account_id, contact_type, or contact_data"
                logger.error(error_msg)
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': 'Invalid request',
                        'message': error_msg
                    })
                }
            
            # Process the account update
            result = self.process_account_update(
                sync_id=sync_id,
                account_id=account_id,
                contact_type=contact_type,
                contact_data=contact_data,
                initiating_user=initiating_user
            )
            
            logger.info(f"Account update completed for {account_id}: {result['status']}")
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'sync_id': sync_id,
                    'account_id': account_id,
                    'status': result['status'],
                    'message': result.get('message', ''),
                    'retry_count': result.get('retry_count', 0)
                })
            }
            
        except Exception as e:
            logger.error(f"Unexpected error in account processor: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Internal server error',
                    'message': str(e)
                })
            }
    
    def process_account_update(self, 
                             sync_id: str,
                             account_id: str, 
                             contact_type: str,
                             contact_data: Dict[str, Any],
                             initiating_user: Optional[str] = None) -> Dict[str, Any]:
        """
        Process contact update for a single member account.
        
        Args:
            sync_id: Unique identifier for the sync operation
            account_id: Target member account ID
            contact_type: Type of contact (primary, billing, operations, security)
            contact_data: Contact information to apply
            initiating_user: User who initiated the change
            
        Returns:
            Dict containing processing results
        """
        logger.info(f"Processing {contact_type} contact update for account {account_id}")
        
        # Initialize result tracking
        result = AccountSyncResult(
            account_id=account_id,
            status="pending",
            timestamp=datetime.now(timezone.utc)
        )
        
        try:
            # Update state tracker with processing start
            self.update_account_result(sync_id, result)
            
            # Parse contact data into appropriate model
            contact_obj = self.parse_contact_data(contact_type, contact_data)
            
            # Process with retry logic
            success = self.process_with_retry(account_id, contact_type, contact_obj, result)
            
            if success:
                result.status = "success"
                result.timestamp = datetime.now(timezone.utc)
                logger.info(f"Successfully updated {contact_type} contact for account {account_id}")
            else:
                result.status = "failed"
                result.timestamp = datetime.now(timezone.utc)
                logger.error(f"Failed to update {contact_type} contact for account {account_id} after {result.retry_count} attempts")
            
            # Update final state
            self.update_account_result(sync_id, result)
            
            return {
                'status': result.status,
                'message': result.error_message or f"Contact update {'succeeded' if success else 'failed'}",
                'retry_count': result.retry_count
            }
            
        except Exception as e:
            logger.error(f"Error processing account {account_id}: {e}")
            result.status = "failed"
            result.error_message = str(e)
            result.timestamp = datetime.now(timezone.utc)
            
            try:
                self.update_account_result(sync_id, result)
            except Exception as state_error:
                logger.error(f"Failed to update state for account {account_id}: {state_error}")
            
            return {
                'status': 'failed',
                'message': str(e),
                'retry_count': result.retry_count
            }
    
    def parse_contact_data(self, contact_type: str, contact_data: Dict[str, Any]) -> Union[ContactInformation, AlternateContact]:
        """
        Parse contact data dictionary into appropriate model.
        
        Args:
            contact_type: Type of contact
            contact_data: Raw contact data dictionary
            
        Returns:
            ContactInformation or AlternateContact object
        """
        try:
            if contact_type.lower() == "primary":
                return ContactInformation(**contact_data)
            else:
                # Alternate contact (billing, operations, security)
                return AlternateContact(**contact_data)
        except Exception as e:
            logger.error(f"Failed to parse contact data for type {contact_type}: {e}")
            raise ValueError(f"Invalid contact data format: {e}")
    
    def process_with_retry(self, 
                          account_id: str, 
                          contact_type: str, 
                          contact_obj: Union[ContactInformation, AlternateContact],
                          result: AccountSyncResult) -> bool:
        """
        Process contact update with retry logic.
        
        Args:
            account_id: Target account ID
            contact_type: Contact type
            contact_obj: Contact information object
            result: Result object to update with retry information
            
        Returns:
            bool: True if successful, False if all retries failed
        """
        for attempt in range(self.max_retry_attempts):
            try:
                result.retry_count = attempt
                
                # Check if update is needed by comparing current contact info
                if not self.is_update_needed(account_id, contact_type, contact_obj):
                    logger.info(f"Contact information for {account_id} is already up to date")
                    return True
                
                # Perform the contact update
                success = self.update_contact_information(account_id, contact_type, contact_obj)
                
                if success:
                    logger.info(f"Successfully updated {contact_type} contact for {account_id} on attempt {attempt + 1}")
                    return True
                else:
                    logger.warning(f"Contact update failed for {account_id} on attempt {attempt + 1}")
                    
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                error_message = e.response.get('Error', {}).get('Message', str(e))
                
                logger.warning(f"AWS API error for {account_id} on attempt {attempt + 1}: {error_code} - {error_message}")
                
                # Check if this is a retryable error
                if not self.is_retryable_error(error_code):
                    logger.error(f"Non-retryable error for {account_id}: {error_code}")
                    result.error_message = f"Permission or configuration error: {error_message}"
                    return False
                
                result.error_message = f"API error: {error_message}"
                
            except Exception as e:
                logger.error(f"Unexpected error for {account_id} on attempt {attempt + 1}: {e}")
                result.error_message = f"Unexpected error: {str(e)}"
            
            # Apply exponential backoff delay before retry (except on last attempt)
            if attempt < self.max_retry_attempts - 1:
                delay = min(self.base_retry_delay * (2 ** attempt), self.max_retry_delay)
                logger.info(f"Retrying {account_id} in {delay} seconds (attempt {attempt + 1}/{self.max_retry_attempts})")
                time.sleep(delay)
        
        logger.error(f"All retry attempts failed for account {account_id}")
        return False
    
    def is_update_needed(self, 
                        account_id: str, 
                        contact_type: str, 
                        new_contact: Union[ContactInformation, AlternateContact]) -> bool:
        """
        Check if contact update is needed by comparing current vs new contact info.
        
        Args:
            account_id: Target account ID
            contact_type: Contact type
            new_contact: New contact information
            
        Returns:
            bool: True if update is needed, False if contacts are identical
        """
        try:
            # Get current contact information
            if contact_type.lower() == "primary":
                current_contact = self.account_mgmt_client.get_contact_information(account_id)
            else:
                current_contact = self.account_mgmt_client.get_alternate_contact(
                    account_id, contact_type.upper()
                )
            
            # If no current contact exists, update is needed
            if current_contact is None:
                logger.info(f"No existing {contact_type} contact found for {account_id}, update needed")
                return True
            
            # Compare contact information
            if self.contacts_are_equal(current_contact, new_contact):
                logger.info(f"Contact information for {account_id} is already up to date")
                return False
            else:
                logger.info(f"Contact information differs for {account_id}, update needed")
                return True
                
        except Exception as e:
            logger.warning(f"Failed to retrieve current contact for {account_id}: {e}")
            # If we can't get current contact, assume update is needed
            return True
    
    def contacts_are_equal(self, 
                          current: Union[ContactInformation, AlternateContact], 
                          new: Union[ContactInformation, AlternateContact]) -> bool:
        """
        Compare two contact objects for equality.
        
        Args:
            current: Current contact information
            new: New contact information
            
        Returns:
            bool: True if contacts are equal
        """
        try:
            # Convert both to dictionaries for comparison
            if hasattr(current, '__dict__'):
                current_dict = current.__dict__
            else:
                from dataclasses import asdict
                current_dict = asdict(current)
            
            if hasattr(new, '__dict__'):
                new_dict = new.__dict__
            else:
                from dataclasses import asdict
                new_dict = asdict(new)
            
            # Compare all fields
            return current_dict == new_dict
            
        except Exception as e:
            logger.warning(f"Error comparing contacts: {e}")
            # If comparison fails, assume they're different
            return False
    
    def update_contact_information(self, 
                                 account_id: str, 
                                 contact_type: str, 
                                 contact_obj: Union[ContactInformation, AlternateContact]) -> bool:
        """
        Update contact information for the specified account.
        
        Args:
            account_id: Target account ID
            contact_type: Contact type
            contact_obj: Contact information to apply
            
        Returns:
            bool: True if successful
        """
        try:
            if contact_type.lower() == "primary":
                self.account_mgmt_client.put_contact_information(account_id, contact_obj)
            else:
                self.account_mgmt_client.put_alternate_contact(account_id, contact_obj)
            
            logger.info(f"Successfully updated {contact_type} contact for account {account_id}")
            return True
            
        except ClientError as e:
            logger.error(f"Failed to update {contact_type} contact for {account_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating contact for {account_id}: {e}")
            raise
    
    def is_retryable_error(self, error_code: str) -> bool:
        """
        Determine if an AWS API error is retryable.
        
        Args:
            error_code: AWS error code
            
        Returns:
            bool: True if error is retryable
        """
        # Retryable errors (temporary issues)
        retryable_errors = {
            'Throttling',
            'ThrottlingException', 
            'RequestLimitExceeded',
            'ServiceUnavailable',
            'InternalServerError',
            'InternalError',
            'RequestTimeout',
            'RequestTimeoutException'
        }
        
        # Non-retryable errors (permission/configuration issues)
        non_retryable_errors = {
            'AccessDenied',
            'UnauthorizedOperation',
            'InvalidParameterValue',
            'ValidationException',
            'ResourceNotFoundException',
            'AccountNotFound'
        }
        
        if error_code in non_retryable_errors:
            return False
        elif error_code in retryable_errors:
            return True
        else:
            # For unknown errors, default to retryable
            logger.warning(f"Unknown error code {error_code}, treating as retryable")
            return True
    
    def update_account_result(self, sync_id: str, result: AccountSyncResult) -> None:
        """
        Update the account result in the state tracker.
        
        Args:
            sync_id: Sync operation ID
            result: Account sync result
        """
        try:
            self.state_tracker.add_account_result(sync_id, result)
        except Exception as e:
            logger.error(f"Failed to update state for sync {sync_id}, account {result.account_id}: {e}")
            # Don't fail the operation if state tracking fails


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda entry point for account processor handler.
    
    Args:
        event: Lambda event containing account update details
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
    handler = AccountProcessorHandler(management_account_id)
    return handler.handle_lambda_event(event, context)