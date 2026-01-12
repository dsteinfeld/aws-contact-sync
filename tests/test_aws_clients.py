"""Unit tests for AWS client wrappers."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError
from datetime import datetime

from src.aws_clients.account_management import AccountManagementClient
from src.aws_clients.organizations import OrganizationsClient, OrganizationAccount
from src.config.config_manager import RetryConfig
from src.models.contact_models import ContactInformation, AlternateContact


class TestAccountManagementClient:
    """Unit tests for AccountManagementClient."""
    
    def test_get_contact_information_success(self):
        """Test successful retrieval of contact information."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock successful API response
        mock_response = {
            'ContactInformation': {
                'AddressLine1': '123 Test St',
                'City': 'Test City',
                'CountryCode': 'US',
                'FullName': 'Test User',
                'PhoneNumber': '+1234567890',
                'PostalCode': '12345',
                'CompanyName': 'Test Company'
            }
        }
        mock_client.get_contact_information.return_value = mock_response
        
        # Test the client
        client = AccountManagementClient(session=mock_session)
        result = client.get_contact_information()
        
        # Verify result
        assert isinstance(result, ContactInformation)
        assert result.full_name == 'Test User'
        assert result.company_name == 'Test Company'
        assert mock_client.get_contact_information.call_count == 1
    
    def test_put_contact_information_success(self):
        """Test successful update of contact information."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Create test contact info
        contact_info = ContactInformation(
            address_line1='123 Test St',
            city='Test City',
            country_code='US',
            full_name='Test User',
            phone_number='+1234567890',
            postal_code='12345'
        )
        
        # Test the client
        client = AccountManagementClient(session=mock_session)
        client.put_contact_information(contact_info, account_id='123456789012')
        
        # Verify API was called correctly
        assert mock_client.put_contact_information.call_count == 1
        call_args = mock_client.put_contact_information.call_args
        assert call_args[1]['AccountId'] == '123456789012'
        assert call_args[1]['ContactInformation']['FullName'] == 'Test User'
    
    def test_get_alternate_contact_success(self):
        """Test successful retrieval of alternate contact."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock successful API response
        mock_response = {
            'AlternateContact': {
                'AlternateContactType': 'BILLING',
                'EmailAddress': 'billing@test.com',
                'Name': 'Billing Contact',
                'PhoneNumber': '+1234567890',
                'Title': 'Billing Manager'
            }
        }
        mock_client.get_alternate_contact.return_value = mock_response
        
        # Test the client
        client = AccountManagementClient(session=mock_session)
        result = client.get_alternate_contact('BILLING')
        
        # Verify result
        assert isinstance(result, AlternateContact)
        assert result.contact_type == 'BILLING'
        assert result.name == 'Billing Contact'
        assert mock_client.get_alternate_contact.call_count == 1
    
    def test_put_alternate_contact_success(self):
        """Test successful update of alternate contact."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Create test alternate contact
        contact = AlternateContact(
            contact_type='OPERATIONS',
            email_address='ops@test.com',
            name='Ops Contact',
            phone_number='+1234567890',
            title='Operations Manager'
        )
        
        # Test the client
        client = AccountManagementClient(session=mock_session)
        client.put_alternate_contact(contact, account_id='123456789012')
        
        # Verify API was called correctly
        assert mock_client.put_alternate_contact.call_count == 1
        call_args = mock_client.put_alternate_contact.call_args
        assert call_args[1]['AccountId'] == '123456789012'
        assert call_args[1]['AlternateContactType'] == 'OPERATIONS'
    
    def test_retry_logic_with_retryable_error(self):
        """Test retry logic with retryable errors."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Configure mock to fail twice then succeed
        retryable_error = ClientError(
            error_response={'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}},
            operation_name='GetContactInformation'
        )
        
        success_response = {
            'ContactInformation': {
                'AddressLine1': '123 Test St',
                'City': 'Test City',
                'CountryCode': 'US',
                'FullName': 'Test User',
                'PhoneNumber': '+1234567890',
                'PostalCode': '12345'
            }
        }
        
        mock_client.get_contact_information.side_effect = [
            retryable_error,
            retryable_error,
            success_response
        ]
        
        # Test with retry config
        retry_config = RetryConfig(max_attempts=3, base_delay=1, max_delay=10)
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        # Should succeed after retries
        result = client.get_contact_information()
        assert isinstance(result, ContactInformation)
        assert mock_client.get_contact_information.call_count == 3
    
    def test_retry_logic_with_non_retryable_error(self):
        """Test that non-retryable errors fail immediately."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Configure mock to raise non-retryable error
        non_retryable_error = ClientError(
            error_response={'Error': {'Code': 'AccessDeniedException', 'Message': 'Access denied'}},
            operation_name='GetContactInformation'
        )
        mock_client.get_contact_information.side_effect = non_retryable_error
        
        # Test with retry config
        retry_config = RetryConfig(max_attempts=3, base_delay=1, max_delay=10)
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        # Should fail immediately without retries
        with pytest.raises(ClientError):
            client.get_contact_information()
        
        assert mock_client.get_contact_information.call_count == 1


class TestOrganizationsClient:
    """Unit tests for OrganizationsClient."""
    
    def test_list_accounts_success(self):
        """Test successful listing of organization accounts."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock successful API response
        mock_response = {
            'Accounts': [
                {
                    'Id': '123456789012',
                    'Name': 'Test Account 1',
                    'Email': 'test1@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'INVITED',
                    'JoinedTimestamp': datetime(2024, 1, 1)
                },
                {
                    'Id': '234567890123',
                    'Name': 'Test Account 2',
                    'Email': 'test2@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'CREATED',
                    'JoinedTimestamp': datetime(2024, 1, 2)
                }
            ]
        }
        mock_client.list_accounts.return_value = mock_response
        
        # Test the client
        client = OrganizationsClient(session=mock_session)
        result = client.list_accounts()
        
        # Verify result
        assert len(result) == 2
        assert all(isinstance(account, OrganizationAccount) for account in result)
        assert result[0].account_id == '123456789012'
        assert result[1].name == 'Test Account 2'
        assert mock_client.list_accounts.call_count == 1
    
    def test_list_accounts_with_pagination(self):
        """Test listing accounts with pagination."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock paginated API responses
        page1_response = {
            'Accounts': [
                {
                    'Id': '123456789012',
                    'Name': 'Test Account 1',
                    'Email': 'test1@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'INVITED',
                    'JoinedTimestamp': datetime(2024, 1, 1)
                }
            ],
            'NextToken': 'token123'
        }
        
        page2_response = {
            'Accounts': [
                {
                    'Id': '234567890123',
                    'Name': 'Test Account 2',
                    'Email': 'test2@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'CREATED',
                    'JoinedTimestamp': datetime(2024, 1, 2)
                }
            ]
        }
        
        mock_client.list_accounts.side_effect = [page1_response, page2_response]
        
        # Test the client
        client = OrganizationsClient(session=mock_session)
        result = client.list_accounts()
        
        # Verify result
        assert len(result) == 2
        assert mock_client.list_accounts.call_count == 2
        
        # Verify pagination was handled correctly
        calls = mock_client.list_accounts.call_args_list
        assert calls[0][1] == {}  # First call without NextToken
        assert calls[1][1] == {'NextToken': 'token123'}  # Second call with NextToken
    
    def test_list_accounts_filter_inactive(self):
        """Test filtering of inactive accounts."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock API response with mixed account statuses
        mock_response = {
            'Accounts': [
                {
                    'Id': '123456789012',
                    'Name': 'Active Account',
                    'Email': 'active@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'INVITED',
                    'JoinedTimestamp': datetime(2024, 1, 1)
                },
                {
                    'Id': '234567890123',
                    'Name': 'Suspended Account',
                    'Email': 'suspended@example.com',
                    'Status': 'SUSPENDED',
                    'JoinedMethod': 'CREATED',
                    'JoinedTimestamp': datetime(2024, 1, 2)
                }
            ]
        }
        mock_client.list_accounts.return_value = mock_response
        
        # Test the client with default filtering (active only)
        client = OrganizationsClient(session=mock_session)
        result = client.list_accounts()
        
        # Verify only active accounts are returned
        assert len(result) == 1
        assert result[0].status == 'ACTIVE'
        assert result[0].name == 'Active Account'
    
    def test_get_account_success(self):
        """Test successful retrieval of specific account."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock successful API response
        mock_response = {
            'Account': {
                'Id': '123456789012',
                'Name': 'Test Account',
                'Email': 'test@example.com',
                'Status': 'ACTIVE',
                'JoinedMethod': 'INVITED',
                'JoinedTimestamp': datetime(2024, 1, 1)
            }
        }
        mock_client.describe_account.return_value = mock_response
        
        # Test the client
        client = OrganizationsClient(session=mock_session)
        result = client.get_account('123456789012')
        
        # Verify result
        assert isinstance(result, OrganizationAccount)
        assert result.account_id == '123456789012'
        assert result.name == 'Test Account'
        assert mock_client.describe_account.call_count == 1
    
    def test_list_active_member_accounts(self):
        """Test listing active member accounts excluding management account."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock list_accounts response
        list_accounts_response = {
            'Accounts': [
                {
                    'Id': '111111111111',  # Management account
                    'Name': 'Management Account',
                    'Email': 'mgmt@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'INVITED',
                    'JoinedTimestamp': datetime(2024, 1, 1)
                },
                {
                    'Id': '222222222222',  # Member account
                    'Name': 'Member Account',
                    'Email': 'member@example.com',
                    'Status': 'ACTIVE',
                    'JoinedMethod': 'CREATED',
                    'JoinedTimestamp': datetime(2024, 1, 2)
                }
            ]
        }
        
        # Mock describe_organization response
        describe_org_response = {
            'Organization': {
                'Id': 'o-example123456',
                'Arn': 'arn:aws:organizations::111111111111:organization/o-example123456',
                'FeatureSet': 'ALL',
                'MasterAccountId': '111111111111',
                'MasterAccountEmail': 'mgmt@example.com'
            }
        }
        
        mock_client.list_accounts.return_value = list_accounts_response
        mock_client.describe_organization.return_value = describe_org_response
        
        # Test the client
        client = OrganizationsClient(session=mock_session)
        result = client.list_active_member_accounts()
        
        # Verify only member accounts are returned (management account excluded)
        assert len(result) == 1
        assert result[0].account_id == '222222222222'
        assert result[0].name == 'Member Account'
    
    def test_get_organization_info_success(self):
        """Test successful retrieval of organization information."""
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Mock successful API response
        mock_response = {
            'Organization': {
                'Id': 'o-example123456',
                'Arn': 'arn:aws:organizations::111111111111:organization/o-example123456',
                'FeatureSet': 'ALL',
                'MasterAccountId': '111111111111',
                'MasterAccountEmail': 'mgmt@example.com'
            }
        }
        mock_client.describe_organization.return_value = mock_response
        
        # Test the client
        client = OrganizationsClient(session=mock_session)
        result = client.get_organization_info()
        
        # Verify result
        assert result['id'] == 'o-example123456'
        assert result['master_account_id'] == '111111111111'
        assert result['feature_set'] == 'ALL'
        assert mock_client.describe_organization.call_count == 1