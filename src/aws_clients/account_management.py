"""AWS Account Management API client wrapper with retry logic."""

import time
import random
from typing import Optional, Dict, Any
from dataclasses import dataclass
import boto3
from botocore.exceptions import ClientError, BotoCoreError
import logging

from ..models.contact_models import ContactInformation, AlternateContact
from ..config.config_manager import RetryConfig
from ..error_handling.recovery_manager import RecoveryManager, RecoveryConfig
from ..error_handling.circuit_breaker import CircuitBreakerConfig

logger = logging.getLogger(__name__)


@dataclass
class RetryableError:
    """Represents an error that can be retried."""
    error_code: str
    is_retryable: bool
    should_backoff: bool


class AccountManagementClient:
    """AWS Account Management API client with retry logic and error handling."""
    
    # Define which errors are retryable
    RETRYABLE_ERRORS = {
        'ThrottlingException': RetryableError('ThrottlingException', True, True),
        'ServiceUnavailableException': RetryableError('ServiceUnavailableException', True, True),
        'InternalServerError': RetryableError('InternalServerError', True, True),
        'RequestTimeout': RetryableError('RequestTimeout', True, True),
        'TooManyRequestsException': RetryableError('TooManyRequestsException', True, True),
        # Non-retryable errors
        'AccessDeniedException': RetryableError('AccessDeniedException', False, False),
        'ValidationException': RetryableError('ValidationException', False, False),
        'ResourceNotFoundException': RetryableError('ResourceNotFoundException', False, False),
        'ConflictException': RetryableError('ConflictException', False, False),
    }
    
    def __init__(self, retry_config: Optional[RetryConfig] = None, session: Optional[boto3.Session] = None):
        """Initialize the Account Management client.
        
        Args:
            retry_config: Configuration for retry logic
            session: Optional boto3 session for testing
        """
        self.retry_config = retry_config or RetryConfig()
        self.session = session or boto3.Session()
        self.client = self.session.client('account')
        
        # Initialize recovery manager with enhanced error handling
        recovery_config = RecoveryConfig(
            max_retry_attempts=self.retry_config.max_attempts,
            base_retry_delay=self.retry_config.base_delay,
            max_retry_delay=self.retry_config.max_delay,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=5,
            circuit_breaker_timeout=60.0
        )
        self.recovery_manager = RecoveryManager(config=recovery_config)
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter.
        
        Args:
            attempt: Current attempt number (0-based)
            
        Returns:
            Delay in seconds
        """
        base_delay = self.retry_config.base_delay
        max_delay = self.retry_config.max_delay
        
        # Exponential backoff: base_delay * (2 ^ attempt)
        delay = min(base_delay * (2 ** attempt), max_delay)
        
        # Add jitter to prevent thundering herd
        jitter = random.uniform(0.1, 0.3) * delay
        return delay + jitter
    
    def _is_retryable_error(self, error: Exception) -> RetryableError:
        """Determine if an error is retryable.
        
        Args:
            error: The exception that occurred
            
        Returns:
            RetryableError indicating if the error can be retried
        """
        if isinstance(error, ClientError):
            error_code = error.response.get('Error', {}).get('Code', 'Unknown')
            return self.RETRYABLE_ERRORS.get(error_code, RetryableError(error_code, False, False))
        elif isinstance(error, BotoCoreError):
            # Network-level errors are generally retryable
            return RetryableError('BotoCoreError', True, True)
        else:
            # Unknown errors are not retryable by default
            return RetryableError('UnknownError', False, False)
    
    def _execute_with_retry(self, operation_name: str, operation_func, *args, **kwargs) -> Any:
        """Execute an operation with retry logic.
        
        Args:
            operation_name: Name of the operation for logging
            operation_func: Function to execute
            *args: Arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function
            
        Returns:
            Result of the operation
            
        Raises:
            Exception: The last exception if all retries are exhausted
        """
        last_exception = None
        
        for attempt in range(self.retry_config.max_attempts):
            try:
                logger.debug(f"Executing {operation_name}, attempt {attempt + 1}/{self.retry_config.max_attempts}")
                result = operation_func(*args, **kwargs)
                
                if attempt > 0:
                    logger.info(f"{operation_name} succeeded after {attempt + 1} attempts")
                
                return result
                
            except Exception as e:
                last_exception = e
                retry_info = self._is_retryable_error(e)
                
                logger.warning(f"{operation_name} failed on attempt {attempt + 1}: {e}")
                
                # If this is the last attempt or error is not retryable, don't retry
                if attempt == self.retry_config.max_attempts - 1 or not retry_info.is_retryable:
                    break
                
                # Calculate and apply backoff delay
                if retry_info.should_backoff:
                    delay = self._calculate_backoff_delay(attempt)
                    logger.debug(f"Backing off for {delay:.2f} seconds before retry")
                    time.sleep(delay)
        
        # All retries exhausted, raise the last exception
        logger.error(f"{operation_name} failed after {self.retry_config.max_attempts} attempts")
        raise last_exception
    
    def get_contact_information(self, account_id: Optional[str] = None) -> ContactInformation:
        """Get primary contact information for an account.
        
        Args:
            account_id: Target account ID. If None, gets info for current account.
            
        Returns:
            ContactInformation object
            
        Raises:
            ClientError: If the API call fails
            ValueError: If the response data is invalid
        """
        def _get_contact():
            kwargs = {}
            if account_id:
                kwargs['AccountId'] = account_id
            
            response = self.client.get_contact_information(**kwargs)
            return response['ContactInformation']
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = f"get_contact_information_{account_id or 'current'}"
            context = {'account_id': account_id, 'operation': 'get_contact_information'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _get_contact, context
            )
            
            if not result.success:
                logger.error(f"Failed to get contact information for account {account_id}: {result.error}")
                raise result.error
            
            contact_data = result.result
            
            # Convert AWS API response to our data model
            return ContactInformation(
                address_line1=contact_data['AddressLine1'],
                address_line2=contact_data.get('AddressLine2'),
                address_line3=contact_data.get('AddressLine3'),
                city=contact_data['City'],
                company_name=contact_data.get('CompanyName'),
                country_code=contact_data['CountryCode'],
                district_or_county=contact_data.get('DistrictOrCounty'),
                full_name=contact_data['FullName'],
                phone_number=contact_data['PhoneNumber'],
                postal_code=contact_data['PostalCode'],
                state_or_region=contact_data.get('StateOrRegion'),
                website_url=contact_data.get('WebsiteUrl')
            )
            
        except Exception as e:
            logger.error(f"Failed to get contact information for account {account_id}: {e}")
            raise
    
    def put_contact_information(self, contact_info: ContactInformation, account_id: Optional[str] = None) -> None:
        """Update primary contact information for an account.
        
        Args:
            contact_info: ContactInformation object with updated data
            account_id: Target account ID. If None, updates current account.
            
        Raises:
            ClientError: If the API call fails
        """
        def _put_contact():
            # Convert our data model to AWS API format
            contact_data = {
                'AddressLine1': contact_info.address_line1,
                'City': contact_info.city,
                'CountryCode': contact_info.country_code,
                'FullName': contact_info.full_name,
                'PhoneNumber': contact_info.phone_number,
                'PostalCode': contact_info.postal_code
            }
            
            # Add optional fields if they exist
            if contact_info.address_line2:
                contact_data['AddressLine2'] = contact_info.address_line2
            if contact_info.address_line3:
                contact_data['AddressLine3'] = contact_info.address_line3
            if contact_info.company_name:
                contact_data['CompanyName'] = contact_info.company_name
            if contact_info.district_or_county:
                contact_data['DistrictOrCounty'] = contact_info.district_or_county
            if contact_info.state_or_region:
                contact_data['StateOrRegion'] = contact_info.state_or_region
            if contact_info.website_url:
                contact_data['WebsiteUrl'] = contact_info.website_url
            
            kwargs = {'ContactInformation': contact_data}
            if account_id:
                kwargs['AccountId'] = account_id
            
            return self.client.put_contact_information(**kwargs)
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = f"put_contact_information_{account_id or 'current'}"
            context = {'account_id': account_id, 'operation': 'put_contact_information'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _put_contact, context
            )
            
            if not result.success:
                logger.error(f"Failed to update contact information for account {account_id}: {result.error}")
                raise result.error
            
            logger.info(f"Successfully updated contact information for account {account_id}")
            
        except Exception as e:
            logger.error(f"Failed to update contact information for account {account_id}: {e}")
            raise
    
    def get_alternate_contact(self, contact_type: str, account_id: Optional[str] = None) -> AlternateContact:
        """Get alternate contact information for an account.
        
        Args:
            contact_type: Type of alternate contact (BILLING, OPERATIONS, SECURITY)
            account_id: Target account ID. If None, gets info for current account.
            
        Returns:
            AlternateContact object
            
        Raises:
            ClientError: If the API call fails
            ValueError: If the contact type is invalid or response data is invalid
        """
        if contact_type not in ['BILLING', 'OPERATIONS', 'SECURITY']:
            raise ValueError(f"Invalid contact type: {contact_type}")
        
        def _get_alternate_contact():
            kwargs = {'AlternateContactType': contact_type}
            if account_id:
                kwargs['AccountId'] = account_id
            
            response = self.client.get_alternate_contact(**kwargs)
            return response['AlternateContact']
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = f"get_alternate_contact_{contact_type}_{account_id or 'current'}"
            context = {'account_id': account_id, 'contact_type': contact_type, 'operation': 'get_alternate_contact'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _get_alternate_contact, context
            )
            
            if not result.success:
                logger.error(f"Failed to get {contact_type} alternate contact for account {account_id}: {result.error}")
                raise result.error
            
            contact_data = result.result
            
            # Convert AWS API response to our data model
            return AlternateContact(
                contact_type=contact_data['AlternateContactType'],
                email_address=contact_data['EmailAddress'],
                name=contact_data['Name'],
                phone_number=contact_data['PhoneNumber'],
                title=contact_data['Title']
            )
            
        except Exception as e:
            # ResourceNotFoundException is expected when no contact exists - not an error
            if isinstance(e, ClientError) and e.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
                logger.info(f"No {contact_type} alternate contact found for account {account_id}")
            else:
                logger.error(f"Failed to get {contact_type} alternate contact for account {account_id}: {e}")
            raise
    
    def put_alternate_contact(self, contact: AlternateContact, account_id: Optional[str] = None) -> None:
        """Update alternate contact information for an account.
        
        Args:
            contact: AlternateContact object with updated data
            account_id: Target account ID. If None, updates current account.
            
        Raises:
            ClientError: If the API call fails
        """
        def _put_alternate_contact():
            kwargs = {
                'AlternateContactType': contact.contact_type,
                'EmailAddress': contact.email_address,
                'Name': contact.name,
                'PhoneNumber': contact.phone_number,
                'Title': contact.title
            }
            if account_id:
                kwargs['AccountId'] = account_id
            
            return self.client.put_alternate_contact(**kwargs)
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = f"put_alternate_contact_{contact.contact_type}_{account_id or 'current'}"
            context = {'account_id': account_id, 'contact_type': contact.contact_type, 'operation': 'put_alternate_contact'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _put_alternate_contact, context
            )
            
            if not result.success:
                logger.error(f"Failed to update {contact.contact_type} alternate contact for account {account_id}: {result.error}")
                raise result.error
            
            logger.info(f"Successfully updated {contact.contact_type} alternate contact for account {account_id}")
            
        except Exception as e:
            logger.error(f"Failed to update {contact.contact_type} alternate contact for account {account_id}: {e}")
            raise
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of the client and its error handling components."""
        return self.recovery_manager.get_health_status()