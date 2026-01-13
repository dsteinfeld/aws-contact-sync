"""AWS Organizations API client wrapper with pagination and filtering."""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import boto3
from botocore.exceptions import ClientError, BotoCoreError
import logging

from ..config.config_manager import RetryConfig
from ..error_handling.recovery_manager import RecoveryManager, RecoveryConfig

logger = logging.getLogger(__name__)


@dataclass
class OrganizationAccount:
    """Represents an AWS organization account."""
    account_id: str
    name: str
    email: str
    status: str
    joined_method: str
    joined_timestamp: str

    def __post_init__(self):
        """Validate account data."""
        if not self.account_id or not self.account_id.isdigit() or len(self.account_id) != 12:
            raise ValueError(f"Invalid account ID format: {self.account_id}")
        if not self.name.strip():
            raise ValueError("Account name cannot be empty")
        if not self.email.strip() or "@" not in self.email:
            raise ValueError(f"Invalid email format: {self.email}")


class OrganizationsClient:
    """AWS Organizations API client with pagination and filtering."""
    
    # Define which errors are retryable (similar to AccountManagementClient)
    RETRYABLE_ERRORS = {
        'ThrottlingException': True,
        'ServiceUnavailableException': True,
        'InternalServerError': True,
        'RequestTimeout': True,
        'TooManyRequestsException': True,
        # Non-retryable errors
        'AccessDeniedException': False,
        'ValidationException': False,
        'AWSOrganizationsNotInUseException': False,
        'AccountNotFoundException': False,
    }
    
    def __init__(self, retry_config: Optional[RetryConfig] = None, session: Optional[boto3.Session] = None):
        """Initialize the Organizations client.
        
        Args:
            retry_config: Configuration for retry logic
            session: Optional boto3 session for testing
        """
        self.retry_config = retry_config or RetryConfig()
        self.session = session or boto3.Session()
        self.client = self.session.client('organizations')
        
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
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error is retryable.
        
        Args:
            error: The exception that occurred
            
        Returns:
            True if the error can be retried
        """
        if isinstance(error, ClientError):
            error_code = error.response.get('Error', {}).get('Code', 'Unknown')
            return self.RETRYABLE_ERRORS.get(error_code, False)
        elif isinstance(error, BotoCoreError):
            # Network-level errors are generally retryable
            return True
        else:
            # Unknown errors are not retryable by default
            return False
    
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
        import time
        import random
        
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
                
                logger.warning(f"{operation_name} failed on attempt {attempt + 1}: {e}")
                
                # If this is the last attempt or error is not retryable, don't retry
                if attempt == self.retry_config.max_attempts - 1 or not self._is_retryable_error(e):
                    break
                
                # Calculate and apply backoff delay
                base_delay = self.retry_config.base_delay
                max_delay = self.retry_config.max_delay
                delay = min(base_delay * (2 ** attempt), max_delay)
                
                # Add jitter to prevent thundering herd
                jitter = random.uniform(0.1, 0.3) * delay
                total_delay = delay + jitter
                
                logger.debug(f"Backing off for {total_delay:.2f} seconds before retry")
                time.sleep(total_delay)
        
        # All retries exhausted, raise the last exception
        logger.error(f"{operation_name} failed after {self.retry_config.max_attempts} attempts")
        raise last_exception
    
    def list_accounts(self, include_inactive: bool = False) -> List[OrganizationAccount]:
        """List all accounts in the organization with pagination support.
        
        Args:
            include_inactive: Whether to include inactive accounts (default: False, only ACTIVE)
            
        Returns:
            List of OrganizationAccount objects
            
        Raises:
            ClientError: If the API call fails
        """
        def _list_accounts_page(next_token: Optional[str] = None) -> Dict[str, Any]:
            """List a single page of accounts."""
            kwargs = {}
            if next_token:
                kwargs['NextToken'] = next_token
            
            return self.client.list_accounts(**kwargs)
        
        def _list_all_accounts():
            """List all accounts with pagination."""
            accounts = []
            next_token = None
            
            while True:
                # Get a page of accounts
                response = _list_accounts_page(next_token)
                
                # Process accounts from this page
                for account_data in response.get('Accounts', []):
                    account_status = account_data.get('Status', '')
                    
                    # Filter by status if requested
                    if not include_inactive and account_status != 'ACTIVE':
                        logger.debug(f"Skipping account {account_data.get('Id')} with status {account_status}")
                        continue
                    
                    try:
                        account = OrganizationAccount(
                            account_id=account_data['Id'],
                            name=account_data['Name'],
                            email=account_data['Email'],
                            status=account_status,
                            joined_method=account_data.get('JoinedMethod', 'UNKNOWN'),
                            joined_timestamp=account_data.get('JoinedTimestamp', '').isoformat() if account_data.get('JoinedTimestamp') else ''
                        )
                        accounts.append(account)
                        
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Skipping invalid account data: {e}")
                        continue
                
                # Check if there are more pages
                next_token = response.get('NextToken')
                if not next_token:
                    break
                
                logger.debug(f"Retrieved {len(response.get('Accounts', []))} accounts, continuing pagination")
            
            return accounts
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = "list_accounts"
            context = {'include_inactive': include_inactive, 'operation': 'list_accounts'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _list_all_accounts, context
            )
            
            if not result.success:
                logger.error(f"Failed to list organization accounts: {result.error}")
                raise result.error
            
            accounts = result.result
            logger.info(f"Successfully retrieved {len(accounts)} accounts from organization")
            return accounts
            
        except Exception as e:
            logger.error(f"Failed to list organization accounts: {e}")
            raise
    
    def get_account(self, account_id: str) -> OrganizationAccount:
        """Get details for a specific account.
        
        Args:
            account_id: The AWS account ID to retrieve
            
        Returns:
            OrganizationAccount object
            
        Raises:
            ClientError: If the API call fails or account is not found
        """
        def _describe_account():
            return self.client.describe_account(AccountId=account_id)
        
        try:
            response = self._execute_with_retry('describe_account', _describe_account)
            account_data = response['Account']
            
            account = OrganizationAccount(
                account_id=account_data['Id'],
                name=account_data['Name'],
                email=account_data['Email'],
                status=account_data.get('Status', 'UNKNOWN'),
                joined_method=account_data.get('JoinedMethod', 'UNKNOWN'),
                joined_timestamp=account_data.get('JoinedTimestamp', '').isoformat() if account_data.get('JoinedTimestamp') else ''
            )
            
            logger.info(f"Successfully retrieved account details for {account_id}")
            return account
            
        except Exception as e:
            logger.error(f"Failed to get account details for {account_id}: {e}")
            raise
    
    def list_active_member_accounts(self, exclude_management_account: bool = True) -> List[OrganizationAccount]:
        """List only active member accounts, optionally excluding the management account.
        
        Args:
            exclude_management_account: Whether to exclude the management account from results
            
        Returns:
            List of active member account OrganizationAccount objects
            
        Raises:
            ClientError: If the API call fails
        """
        try:
            # Get all active accounts
            all_accounts = self.list_accounts(include_inactive=False)
            
            if not exclude_management_account:
                return all_accounts
            
            # Filter out the management account
            # The management account is typically the one with JoinedMethod = 'INVITED' and earliest timestamp,
            # but we'll use the Organizations API to be sure
            try:
                def _describe_organization():
                    return self.client.describe_organization()
                
                org_response = self._execute_with_retry('describe_organization', _describe_organization)
                management_account_id = org_response['Organization']['MasterAccountId']
                
                member_accounts = [
                    account for account in all_accounts 
                    if account.account_id != management_account_id
                ]
                
                logger.info(f"Retrieved {len(member_accounts)} member accounts (excluding management account {management_account_id})")
                return member_accounts
                
            except Exception as e:
                logger.warning(f"Could not determine management account ID: {e}")
                # Fallback: return all accounts if we can't determine the management account
                return all_accounts
            
        except Exception as e:
            logger.error(f"Failed to list active member accounts: {e}")
            raise
    
    def get_organization_info(self) -> Dict[str, Any]:
        """Get basic information about the organization.
        
        Returns:
            Dictionary containing organization information
            
        Raises:
            ClientError: If the API call fails
        """
        def _describe_organization():
            return self.client.describe_organization()
        
        try:
            # Use recovery manager for enhanced error handling
            operation_name = "describe_organization"
            context = {'operation': 'describe_organization'}
            
            result = self.recovery_manager.execute_with_recovery(
                operation_name, _describe_organization, context
            )
            
            if not result.success:
                logger.error(f"Failed to get organization information: {result.error}")
                raise result.error
            
            response = result.result
            org_data = response['Organization']
            
            info = {
                'id': org_data['Id'],
                'arn': org_data['Arn'],
                'feature_set': org_data.get('FeatureSet', 'UNKNOWN'),
                'master_account_id': org_data['MasterAccountId'],
                'master_account_email': org_data['MasterAccountEmail']
            }
            
            logger.info(f"Successfully retrieved organization info for {info['id']}")
            return info
            
        except Exception as e:
            logger.error(f"Failed to get organization information: {e}")
            raise
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of the client and its error handling components."""
        return self.recovery_manager.get_health_status()