"""
End-to-end integration tests for AWS Contact Synchronization workflows.

These tests simulate real contact synchronization scenarios and validate
the complete workflow from event detection to member account updates.
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import boto3
import pytest
from botocore.exceptions import ClientError


@pytest.fixture(scope="session")
def aws_region():
    """Get AWS region from environment."""
    return os.environ.get('AWS_REGION', 'us-east-1')


@pytest.fixture(scope="session")
def management_account_id():
    """Get management account ID from STS."""
    sts_client = boto3.client('sts')
    return sts_client.get_caller_identity()['Account']


@pytest.fixture(scope="session")
def organizations_client(aws_region):
    """Organizations client for account operations."""
    return boto3.client('organizations', region_name=aws_region)


@pytest.fixture(scope="session")
def account_client(aws_region):
    """Account Management client for contact operations."""
    return boto3.client('account', region_name=aws_region)


@pytest.fixture(scope="session")
def lambda_client(aws_region):
    """Lambda client for function invocation."""
    return boto3.client('lambda', region_name=aws_region)


@pytest.fixture(scope="session")
def dynamodb_client(aws_region):
    """DynamoDB client for state tracking."""
    return boto3.client('dynamodb', region_name=aws_region)


@pytest.fixture(scope="session")
def stack_outputs():
    """Get stack outputs from environment or CloudFormation."""
    stack_name = os.environ.get('STACK_NAME')
    if not stack_name:
        pytest.skip("STACK_NAME environment variable not set")
    
    cf_client = boto3.client('cloudformation')
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        stack = response['Stacks'][0]
        
        outputs = {}
        for output in stack.get('Outputs', []):
            outputs[output['OutputKey']] = output['OutputValue']
        
        return outputs
    except ClientError:
        pytest.skip(f"Stack {stack_name} not found")


@pytest.fixture
def test_contact_information():
    """Generate test contact information."""
    test_id = uuid.uuid4().hex[:8]
    return {
        'fullName': f"Test User {test_id}",
        'phoneNumber': f"+1-555-{test_id[:4]}",
        'addressLine1': f"{test_id[:3]} Test Street",
        'city': "Test City",
        'countryCode': "US",
        'postalCode': "12345"
    }


@pytest.fixture
def test_alternate_contact():
    """Generate test alternate contact."""
    test_id = uuid.uuid4().hex[:8]
    return {
        'contactType': "BILLING",
        'name': f"Test Billing Contact {test_id}",
        'emailAddress': f"billing-{test_id}@example.com",
        'phoneNumber': f"+1-555-{test_id[:4]}",
        'title': "Billing Manager"
    }


class TestOrganizationDiscovery:
    """Test organization member account discovery."""
    
    def test_list_organization_accounts(self, organizations_client, management_account_id):
        """Test listing organization accounts."""
        try:
            response = organizations_client.list_accounts()
            accounts = response['Accounts']
            
            # Should have at least the management account
            assert len(accounts) >= 1
            
            # Find management account
            mgmt_account = next(
                (acc for acc in accounts if acc['Id'] == management_account_id),
                None
            )
            assert mgmt_account is not None
            assert mgmt_account['Status'] == 'ACTIVE'
            
            # Check for member accounts
            member_accounts = [
                acc for acc in accounts 
                if acc['Id'] != management_account_id and acc['Status'] == 'ACTIVE'
            ]
            
            if member_accounts:
                pytest.current_member_accounts = [acc['Id'] for acc in member_accounts]
            else:
                pytest.current_member_accounts = []
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'AccessDeniedException':
                pytest.skip("No access to Organizations API - single account setup")
            raise
    
    def test_describe_organization(self, organizations_client):
        """Test describing the organization."""
        try:
            response = organizations_client.describe_organization()
            org = response['Organization']
            
            assert 'Id' in org
            assert 'MasterAccountId' in org
            assert org['FeatureSet'] in ['ALL', 'CONSOLIDATED_BILLING']
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'AWSOrganizationsNotInUseException':
                pytest.skip("Account is not part of an organization")
            raise


class TestContactManagementAPI:
    """Test Account Management API operations."""
    
    def test_get_current_contact_information(self, account_client, management_account_id):
        """Test retrieving current contact information."""
        try:
            response = account_client.get_contact_information()
            contact_info = response['ContactInformation']
            
            # Verify required fields are present
            required_fields = ['FullName', 'PhoneNumber', 'AddressLine1', 'City', 'CountryCode', 'PostalCode']
            for field in required_fields:
                assert field in contact_info
                assert contact_info[field]  # Should not be empty
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'AccessDeniedException':
                pytest.skip("No access to Account Management API")
            raise
    
    def test_get_alternate_contacts(self, account_client):
        """Test retrieving alternate contacts."""
        contact_types = ['BILLING', 'OPERATIONS', 'SECURITY']
        
        for contact_type in contact_types:
            try:
                response = account_client.get_alternate_contact(
                    AlternateContactType=contact_type
                )
                
                if 'AlternateContact' in response:
                    contact = response['AlternateContact']
                    assert contact['AlternateContactType'] == contact_type
                    assert 'Name' in contact
                    assert 'EmailAddress' in contact
                    
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    # Alternate contact doesn't exist - this is fine
                    continue
                elif e.response['Error']['Code'] == 'AccessDeniedException':
                    pytest.skip(f"No access to {contact_type} alternate contact")
                raise


class TestLambdaFunctionWorkflows:
    """Test Lambda function workflows with realistic scenarios."""
    
    def test_contact_sync_handler_with_valid_event(self, lambda_client, stack_outputs, 
                                                  management_account_id, test_contact_information):
        """Test contact sync handler with a valid contact change event."""
        function_arn = stack_outputs.get('ContactSyncHandlerArn')
        if not function_arn:
            pytest.skip("ContactSyncHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create realistic contact change event
        event = {
            'Records': [{
                'eventSource': 'aws:events',
                'eventName': 'ContactChangeEvent',
                'detail': {
                    'eventID': f'test-{uuid.uuid4().hex}',
                    'eventName': 'PutContactInformation',
                    'eventTime': datetime.utcnow().isoformat() + 'Z',
                    'userIdentity': {
                        'type': 'IAMUser',
                        'principalId': 'test-principal-id',
                        'arn': f'arn:aws:iam::{management_account_id}:user/test-user'
                    },
                    'recipientAccountId': management_account_id,
                    'requestParameters': {
                        'contactInformation': test_contact_information
                    }
                }
            }]
        }
        
        # Invoke function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        assert response['StatusCode'] == 200
        
        # Parse response payload
        payload = json.loads(response['Payload'].read())
        assert 'statusCode' in payload
        
        # Should succeed or return 200 with appropriate message
        if payload['statusCode'] == 200:
            body = json.loads(payload['body'])
            assert 'message' in body
        else:
            # Log error for debugging
            print(f"Function returned status {payload['statusCode']}: {payload.get('body', '')}")
    
    def test_contact_sync_handler_with_member_account_event(self, lambda_client, stack_outputs, 
                                                           management_account_id):
        """Test that member account events are properly filtered out."""
        function_arn = stack_outputs.get('ContactSyncHandlerArn')
        if not function_arn:
            pytest.skip("ContactSyncHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create event that should be filtered out (has accountId in requestParameters)
        event = {
            'Records': [{
                'eventSource': 'aws:events',
                'eventName': 'ContactChangeEvent',
                'detail': {
                    'eventID': f'test-member-{uuid.uuid4().hex}',
                    'eventName': 'PutContactInformation',
                    'eventTime': datetime.utcnow().isoformat() + 'Z',
                    'userIdentity': {
                        'type': 'AssumedRole',
                        'principalId': 'test-role-principal',
                        'arn': f'arn:aws:sts::{management_account_id}:assumed-role/ContactSyncRole/session'
                    },
                    'recipientAccountId': management_account_id,
                    'requestParameters': {
                        'accountId': '111111111111',  # This indicates member account operation
                        'contactInformation': {
                            'fullName': 'Member Account Contact',
                            'phoneNumber': '+1-555-9999'
                        }
                    }
                }
            }]
        }
        
        # Invoke function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        assert response['StatusCode'] == 200
        
        # Should return success but with no events processed
        payload = json.loads(response['Payload'].read())
        assert payload['statusCode'] == 200
        
        body = json.loads(payload['body'])
        assert body['processed_events'] == 0
    
    def test_account_processor_handler_workflow(self, lambda_client, stack_outputs, 
                                               management_account_id, test_contact_information):
        """Test account processor handler with realistic account update."""
        function_arn = stack_outputs.get('AccountProcessorHandlerArn')
        if not function_arn:
            pytest.skip("AccountProcessorHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create account processor event
        event = {
            'sync_id': f'test-sync-{uuid.uuid4().hex[:8]}',
            'account_id': management_account_id,  # Use management account for testing
            'contact_type': 'primary',
            'contact_data': test_contact_information,
            'initiating_user': f'arn:aws:iam::{management_account_id}:user/integration-test'
        }
        
        # Invoke function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        assert response['StatusCode'] == 200
        
        # Parse response
        payload = json.loads(response['Payload'].read())
        assert 'statusCode' in payload
        
        if payload['statusCode'] == 200:
            body = json.loads(payload['body'])
            assert 'sync_id' in body
            assert 'account_id' in body
            assert 'status' in body
            assert body['sync_id'] == event['sync_id']
            assert body['account_id'] == event['account_id']


class TestStateTrackingWorkflows:
    """Test state tracking and audit functionality."""
    
    def test_sync_operation_lifecycle(self, dynamodb_client, stack_outputs):
        """Test complete sync operation lifecycle in state table."""
        state_table_name = stack_outputs.get('StateTableName')
        if not state_table_name:
            pytest.skip("StateTableName not found in stack outputs")
        
        sync_id = f'lifecycle-test-{uuid.uuid4().hex[:8]}'
        timestamp = datetime.utcnow().isoformat()
        
        # 1. Create initial sync operation
        initial_operation = {
            'sync_id': {'S': sync_id},
            'timestamp': {'S': timestamp},
            'initiating_user': {'S': 'arn:aws:iam::123456789012:user/test-user'},
            'contact_type': {'S': 'primary'},
            'source_account': {'S': '123456789012'},
            'target_accounts': {'SS': ['111111111111', '222222222222']},
            'status': {'S': 'pending'},
            'ttl': {'N': str(int((datetime.utcnow() + timedelta(days=90)).timestamp()))}
        }
        
        dynamodb_client.put_item(
            TableName=state_table_name,
            Item=initial_operation
        )
        
        # 2. Update to in_progress
        dynamodb_client.update_item(
            TableName=state_table_name,
            Key={'sync_id': {'S': sync_id}},
            UpdateExpression='SET #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': {'S': 'in_progress'}}
        )
        
        # 3. Add account results
        account_results = {
            '111111111111': {
                'M': {
                    'account_id': {'S': '111111111111'},
                    'status': {'S': 'success'},
                    'timestamp': {'S': datetime.utcnow().isoformat()},
                    'retry_count': {'N': '0'}
                }
            },
            '222222222222': {
                'M': {
                    'account_id': {'S': '222222222222'},
                    'status': {'S': 'failed'},
                    'timestamp': {'S': datetime.utcnow().isoformat()},
                    'error_message': {'S': 'Access denied'},
                    'retry_count': {'N': '3'}
                }
            }
        }
        
        dynamodb_client.update_item(
            TableName=state_table_name,
            Key={'sync_id': {'S': sync_id}},
            UpdateExpression='SET results = :results, #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':results': {'M': account_results},
                ':status': {'S': 'completed'}
            }
        )
        
        # 4. Verify final state
        response = dynamodb_client.get_item(
            TableName=state_table_name,
            Key={'sync_id': {'S': sync_id}}
        )
        
        assert 'Item' in response
        final_operation = response['Item']
        
        assert final_operation['status']['S'] == 'completed'
        assert 'results' in final_operation
        assert '111111111111' in final_operation['results']['M']
        assert '222222222222' in final_operation['results']['M']
        
        # Verify account results
        results = final_operation['results']['M']
        assert results['111111111111']['M']['status']['S'] == 'success'
        assert results['222222222222']['M']['status']['S'] == 'failed'
        assert results['222222222222']['M']['error_message']['S'] == 'Access denied'
        
        # 5. Test querying by status
        response = dynamodb_client.query(
            TableName=state_table_name,
            IndexName='status-timestamp-index',
            KeyConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': {'S': 'completed'}}
        )
        
        assert response['Count'] >= 1
        
        # Cleanup
        dynamodb_client.delete_item(
            TableName=state_table_name,
            Key={'sync_id': {'S': sync_id}}
        )
    
    def test_audit_trail_queries(self, dynamodb_client, stack_outputs):
        """Test audit trail query capabilities."""
        state_table_name = stack_outputs.get('StateTableName')
        if not state_table_name:
            pytest.skip("StateTableName not found in stack outputs")
        
        # Create multiple test operations for different time periods
        base_time = datetime.utcnow()
        test_operations = []
        
        for i in range(3):
            sync_id = f'audit-test-{i}-{uuid.uuid4().hex[:8]}'
            timestamp = (base_time - timedelta(hours=i)).isoformat()
            
            operation = {
                'sync_id': {'S': sync_id},
                'timestamp': {'S': timestamp},
                'initiating_user': {'S': f'arn:aws:iam::123456789012:user/test-user-{i}'},
                'contact_type': {'S': 'primary'},
                'source_account': {'S': '123456789012'},
                'status': {'S': 'completed'},
                'ttl': {'N': str(int((datetime.utcnow() + timedelta(days=90)).timestamp()))}
            }
            
            dynamodb_client.put_item(
                TableName=state_table_name,
                Item=operation
            )
            test_operations.append(sync_id)
        
        # Query by timestamp range
        start_time = (base_time - timedelta(hours=4)).isoformat()
        end_time = base_time.isoformat()
        
        response = dynamodb_client.query(
            TableName=state_table_name,
            IndexName='timestamp-index',
            KeyConditionExpression='#ts BETWEEN :start_time AND :end_time',
            ExpressionAttributeNames={'#ts': 'timestamp'},
            ExpressionAttributeValues={
                ':start_time': {'S': start_time},
                ':end_time': {'S': end_time}
            }
        )
        
        # Should find our test operations
        found_operations = [item['sync_id']['S'] for item in response['Items']]
        for test_sync_id in test_operations:
            if test_sync_id in found_operations:
                # At least one of our test operations should be found
                break
        else:
            pytest.fail("None of the test operations were found in timestamp query")
        
        # Cleanup
        for sync_id in test_operations:
            dynamodb_client.delete_item(
                TableName=state_table_name,
                Key={'sync_id': {'S': sync_id}}
            )


class TestErrorHandlingWorkflows:
    """Test error handling and resilience workflows."""
    
    def test_invalid_contact_data_handling(self, lambda_client, stack_outputs, management_account_id):
        """Test handling of invalid contact data."""
        function_arn = stack_outputs.get('AccountProcessorHandlerArn')
        if not function_arn:
            pytest.skip("AccountProcessorHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create event with invalid contact data
        event = {
            'sync_id': f'invalid-test-{uuid.uuid4().hex[:8]}',
            'account_id': management_account_id,
            'contact_type': 'primary',
            'contact_data': {
                'fullName': '',  # Invalid: empty name
                'phoneNumber': 'invalid-phone',  # Invalid format
                'addressLine1': '',  # Invalid: empty address
                'city': '',  # Invalid: empty city
                'countryCode': 'INVALID',  # Invalid country code
                'postalCode': ''  # Invalid: empty postal code
            },
            'initiating_user': f'arn:aws:iam::{management_account_id}:user/test'
        }
        
        # Invoke function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        assert response['StatusCode'] == 200
        
        # Should handle invalid data gracefully
        payload = json.loads(response['Payload'].read())
        # Function should return error status but not crash
        assert 'statusCode' in payload
    
    def test_nonexistent_account_handling(self, lambda_client, stack_outputs):
        """Test handling of nonexistent target accounts."""
        function_arn = stack_outputs.get('AccountProcessorHandlerArn')
        if not function_arn:
            pytest.skip("AccountProcessorHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create event with nonexistent account
        event = {
            'sync_id': f'nonexistent-test-{uuid.uuid4().hex[:8]}',
            'account_id': '999999999999',  # Nonexistent account
            'contact_type': 'primary',
            'contact_data': {
                'fullName': 'Test User',
                'phoneNumber': '+1-555-0123',
                'addressLine1': '123 Test St',
                'city': 'Test City',
                'countryCode': 'US',
                'postalCode': '12345'
            },
            'initiating_user': 'arn:aws:iam::123456789012:user/test'
        }
        
        # Invoke function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        assert response['StatusCode'] == 200
        
        # Should handle nonexistent account gracefully
        payload = json.loads(response['Payload'].read())
        assert 'statusCode' in payload
        
        # Should return error but not crash
        if payload['statusCode'] == 200:
            body = json.loads(payload['body'])
            # Should indicate failure
            assert body.get('status') in ['failed', 'error']


@pytest.mark.slow
class TestPerformanceWorkflows:
    """Test performance characteristics of workflows."""
    
    def test_large_organization_simulation(self, lambda_client, stack_outputs, management_account_id):
        """Test handling of large organization with many member accounts."""
        function_arn = stack_outputs.get('ContactSyncHandlerArn')
        if not function_arn:
            pytest.skip("ContactSyncHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create event that would trigger sync to many accounts
        event = {
            'Records': [{
                'eventSource': 'aws:events',
                'eventName': 'ContactChangeEvent',
                'detail': {
                    'eventID': f'perf-test-{uuid.uuid4().hex}',
                    'eventName': 'PutContactInformation',
                    'eventTime': datetime.utcnow().isoformat() + 'Z',
                    'userIdentity': {
                        'type': 'IAMUser',
                        'principalId': 'perf-test-principal',
                        'arn': f'arn:aws:iam::{management_account_id}:user/perf-test-user'
                    },
                    'recipientAccountId': management_account_id,
                    'requestParameters': {
                        'contactInformation': {
                            'fullName': 'Performance Test User',
                            'phoneNumber': '+1-555-PERF',
                            'addressLine1': '123 Performance Ave',
                            'city': 'Test City',
                            'countryCode': 'US',
                            'postalCode': '12345'
                        }
                    }
                }
            }]
        }
        
        # Measure execution time
        start_time = time.time()
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(event)
        )
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        assert response['StatusCode'] == 200
        
        # Should complete within reasonable time (30 seconds for contact sync handler)
        assert execution_time < 30.0, f"Execution took too long: {execution_time:.2f} seconds"
        
        # Parse response
        payload = json.loads(response['Payload'].read())
        assert 'statusCode' in payload
    
    def test_concurrent_sync_operations(self, lambda_client, stack_outputs, management_account_id):
        """Test handling of concurrent synchronization operations."""
        function_arn = stack_outputs.get('ContactSyncHandlerArn')
        if not function_arn:
            pytest.skip("ContactSyncHandlerArn not found in stack outputs")
        
        function_name = function_arn.split(':')[-1]
        
        # Create multiple concurrent events
        events = []
        for i in range(3):
            event = {
                'Records': [{
                    'eventSource': 'aws:events',
                    'eventName': 'ContactChangeEvent',
                    'detail': {
                        'eventID': f'concurrent-test-{i}-{uuid.uuid4().hex}',
                        'eventName': 'PutContactInformation',
                        'eventTime': datetime.utcnow().isoformat() + 'Z',
                        'userIdentity': {
                            'type': 'IAMUser',
                            'principalId': f'concurrent-test-principal-{i}',
                            'arn': f'arn:aws:iam::{management_account_id}:user/concurrent-test-{i}'
                        },
                        'recipientAccountId': management_account_id,
                        'requestParameters': {
                            'contactInformation': {
                                'fullName': f'Concurrent Test User {i}',
                                'phoneNumber': f'+1-555-{i:04d}',
                                'addressLine1': f'{i} Concurrent Ave',
                                'city': 'Test City',
                                'countryCode': 'US',
                                'postalCode': '12345'
                            }
                        }
                    }
                }]
            }
            events.append(event)
        
        # Invoke functions concurrently (asynchronously)
        responses = []
        for event in events:
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',  # Asynchronous invocation
                Payload=json.dumps(event)
            )
            responses.append(response)
        
        # All invocations should be accepted
        for response in responses:
            assert response['StatusCode'] == 202  # Accepted for async invocation
        
        # Wait a moment for processing
        time.sleep(5)
        
        # Note: For async invocations, we can't directly verify the results
        # In a real scenario, you would check CloudWatch logs or DynamoDB state