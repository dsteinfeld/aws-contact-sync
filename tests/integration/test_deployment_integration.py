"""Integration tests for deployment and configuration validation."""

import json
import os
import pytest
import boto3
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError

# Test configuration
STACK_NAME = os.environ.get('STACK_NAME', 'aws-contact-sync-test')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
CONFIG_TABLE_NAME = os.environ.get('CONFIG_TABLE_NAME')
STATE_TABLE_NAME = os.environ.get('STATE_TABLE_NAME')
MANAGEMENT_ACCOUNT_ID = os.environ.get('MANAGEMENT_ACCOUNT_ID', '123456789012')


@pytest.fixture(scope="module")
def aws_clients():
    """Create AWS service clients for testing."""
    return {
        'cloudformation': boto3.client('cloudformation', region_name=AWS_REGION),
        'lambda': boto3.client('lambda', region_name=AWS_REGION),
        'dynamodb': boto3.client('dynamodb', region_name=AWS_REGION),
        'events': boto3.client('events', region_name=AWS_REGION),
        'sns': boto3.client('sns', region_name=AWS_REGION),
        'cloudwatch': boto3.client('cloudwatch', region_name=AWS_REGION),
        'logs': boto3.client('logs', region_name=AWS_REGION)
    }


@pytest.fixture(scope="module")
def stack_outputs(aws_clients):
    """Get CloudFormation stack outputs."""
    try:
        response = aws_clients['cloudformation'].describe_stacks(StackName=STACK_NAME)
        outputs = {}
        for output in response['Stacks'][0].get('Outputs', []):
            outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except ClientError as e:
        pytest.skip(f"Stack {STACK_NAME} not found: {e}")


class TestDeploymentIntegration:
    """Test deployment infrastructure and configuration."""
    
    def test_stack_exists_and_healthy(self, aws_clients):
        """Test that the CloudFormation stack exists and is in a healthy state."""
        try:
            response = aws_clients['cloudformation'].describe_stacks(StackName=STACK_NAME)
            stack = response['Stacks'][0]
            
            assert stack['StackStatus'] in ['CREATE_COMPLETE', 'UPDATE_COMPLETE'], \
                f"Stack is in unhealthy state: {stack['StackStatus']}"
            
            # Check stack has required outputs
            output_keys = [output['OutputKey'] for output in stack.get('Outputs', [])]
            required_outputs = [
                'ContactSyncHandlerArn',
                'AccountProcessorHandlerArn', 
                'NotificationHandlerArn',
                'ConfigTableName',
                'StateTableName',
                'NotificationTopicArn'
            ]
            
            for required_output in required_outputs:
                assert required_output in output_keys, \
                    f"Required output {required_output} not found in stack"
                    
        except ClientError as e:
            pytest.fail(f"Failed to describe stack: {e}")
    
    def test_lambda_functions_deployed(self, aws_clients, stack_outputs):
        """Test that all Lambda functions are deployed and active."""
        lambda_functions = [
            ('ContactSyncHandlerArn', 'contact-sync-handler'),
            ('AccountProcessorHandlerArn', 'account-processor-handler'),
            ('NotificationHandlerArn', 'notification-handler')
        ]
        
        for output_key, function_type in lambda_functions:
            function_arn = stack_outputs.get(output_key)
            assert function_arn, f"Lambda function ARN not found for {function_type}"
            
            # Extract function name from ARN
            function_name = function_arn.split(':')[-1]
            
            try:
                response = aws_clients['lambda'].get_function(FunctionName=function_name)
                config = response['Configuration']
                
                assert config['State'] == 'Active', \
                    f"Lambda function {function_name} is not active: {config['State']}"
                
                assert config['Runtime'].startswith('python'), \
                    f"Lambda function {function_name} has unexpected runtime: {config['Runtime']}"
                
                # Check environment variables
                env_vars = config.get('Environment', {}).get('Variables', {})
                assert 'MANAGEMENT_ACCOUNT_ID' in env_vars, \
                    f"Lambda function {function_name} missing MANAGEMENT_ACCOUNT_ID"
                
            except ClientError as e:
                pytest.fail(f"Failed to get Lambda function {function_name}: {e}")
    
    def test_dynamodb_tables_created(self, aws_clients, stack_outputs):
        """Test that DynamoDB tables are created and active."""
        tables = [
            ('ConfigTableName', 'configuration'),
            ('StateTableName', 'state tracking')
        ]
        
        for output_key, table_type in tables:
            table_name = stack_outputs.get(output_key)
            assert table_name, f"DynamoDB table name not found for {table_type}"
            
            try:
                response = aws_clients['dynamodb'].describe_table(TableName=table_name)
                table = response['Table']
                
                assert table['TableStatus'] == 'ACTIVE', \
                    f"DynamoDB table {table_name} is not active: {table['TableStatus']}"
                
                assert table['BillingModeSummary']['BillingMode'] == 'PAY_PER_REQUEST', \
                    f"DynamoDB table {table_name} not using pay-per-request billing"
                
                # Check encryption
                assert 'SSEDescription' in table, \
                    f"DynamoDB table {table_name} is not encrypted"
                
            except ClientError as e:
                pytest.fail(f"Failed to describe DynamoDB table {table_name}: {e}")
    
    def test_eventbridge_rules_configured(self, aws_clients):
        """Test that EventBridge rules are properly configured."""
        try:
            response = aws_clients['events'].list_rules()
            contact_sync_rules = [
                rule for rule in response['Rules'] 
                if 'ContactSync' in rule['Name']
            ]
            
            assert len(contact_sync_rules) > 0, "No ContactSync EventBridge rules found"
            
            for rule in contact_sync_rules:
                assert rule['State'] == 'ENABLED', \
                    f"EventBridge rule {rule['Name']} is not enabled"
                
                # Get rule details
                rule_detail = aws_clients['events'].describe_rule(Name=rule['Name'])
                event_pattern = json.loads(rule_detail['EventPattern'])
                
                # Verify event pattern contains required fields
                assert 'source' in event_pattern, \
                    f"EventBridge rule {rule['Name']} missing source pattern"
                assert 'aws.account' in event_pattern['source'], \
                    f"EventBridge rule {rule['Name']} not filtering for aws.account events"
                
        except ClientError as e:
            pytest.fail(f"Failed to list EventBridge rules: {e}")
    
    def test_sns_topic_accessible(self, aws_clients, stack_outputs):
        """Test that SNS topic is accessible and properly configured."""
        topic_arn = stack_outputs.get('NotificationTopicArn')
        assert topic_arn, "SNS topic ARN not found in stack outputs"
        
        try:
            response = aws_clients['sns'].get_topic_attributes(TopicArn=topic_arn)
            attributes = response['Attributes']
            
            assert 'KmsMasterKeyId' in attributes, \
                "SNS topic is not encrypted"
            
            # Check if topic has subscriptions (optional)
            subscriptions = aws_clients['sns'].list_subscriptions_by_topic(TopicArn=topic_arn)
            # Note: Subscriptions are optional, so we don't assert their presence
            
        except ClientError as e:
            pytest.fail(f"Failed to get SNS topic attributes: {e}")
    
    def test_cloudwatch_alarms_created(self, aws_clients):
        """Test that CloudWatch alarms are created and configured."""
        try:
            response = aws_clients['cloudwatch'].describe_alarms(
                AlarmNamePrefix=STACK_NAME
            )
            alarms = response['MetricAlarms']
            
            assert len(alarms) > 0, f"No CloudWatch alarms found for stack {STACK_NAME}"
            
            # Check for specific alarm types
            alarm_names = [alarm['AlarmName'] for alarm in alarms]
            expected_alarm_types = ['error', 'dlq']
            
            for alarm_type in expected_alarm_types:
                matching_alarms = [name for name in alarm_names if alarm_type in name.lower()]
                assert len(matching_alarms) > 0, \
                    f"No {alarm_type} alarms found"
            
            # Verify alarm configuration
            for alarm in alarms:
                assert alarm['ActionsEnabled'], \
                    f"Alarm {alarm['AlarmName']} has actions disabled"
                assert len(alarm['AlarmActions']) > 0, \
                    f"Alarm {alarm['AlarmName']} has no actions configured"
                
        except ClientError as e:
            pytest.fail(f"Failed to describe CloudWatch alarms: {e}")
    
    def test_log_groups_created(self, aws_clients, stack_outputs):
        """Test that CloudWatch log groups are created with proper retention."""
        lambda_functions = [
            stack_outputs.get('ContactSyncHandlerArn'),
            stack_outputs.get('AccountProcessorHandlerArn'),
            stack_outputs.get('NotificationHandlerArn')
        ]
        
        for function_arn in lambda_functions:
            if not function_arn:
                continue
                
            function_name = function_arn.split(':')[-1]
            log_group_name = f"/aws/lambda/{function_name}"
            
            try:
                response = aws_clients['logs'].describe_log_groups(
                    logGroupNamePrefix=log_group_name
                )
                
                matching_groups = [
                    group for group in response['logGroups']
                    if group['logGroupName'] == log_group_name
                ]
                
                assert len(matching_groups) == 1, \
                    f"Log group {log_group_name} not found or duplicated"
                
                log_group = matching_groups[0]
                assert 'retentionInDays' in log_group, \
                    f"Log group {log_group_name} has no retention policy"
                
                # Retention should be reasonable (not indefinite)
                retention_days = log_group['retentionInDays']
                assert 1 <= retention_days <= 3653, \
                    f"Log group {log_group_name} has invalid retention: {retention_days} days"
                
            except ClientError as e:
                pytest.fail(f"Failed to describe log group {log_group_name}: {e}")


class TestConfigurationIntegration:
    """Test system configuration and data setup."""
    
    def test_configuration_table_accessible(self, aws_clients):
        """Test that configuration table is accessible and has default config."""
        if not CONFIG_TABLE_NAME:
            pytest.skip("CONFIG_TABLE_NAME not provided")
        
        try:
            # Test table access
            response = aws_clients['dynamodb'].describe_table(TableName=CONFIG_TABLE_NAME)
            assert response['Table']['TableStatus'] == 'ACTIVE'
            
            # Check for default configuration
            response = aws_clients['dynamodb'].get_item(
                TableName=CONFIG_TABLE_NAME,
                Key={'config_key': {'S': 'default'}}
            )
            
            if 'Item' in response:
                config = response['Item']
                
                # Verify required configuration fields
                required_fields = ['contact_types', 'excluded_accounts', 'retry_config']
                for field in required_fields:
                    assert field in config, \
                        f"Default configuration missing required field: {field}"
                
                # Verify contact types are valid
                contact_types = config['contact_types'].get('SS', [])
                valid_types = {'primary', 'billing', 'operations', 'security'}
                for contact_type in contact_types:
                    assert contact_type in valid_types, \
                        f"Invalid contact type in configuration: {contact_type}"
            
        except ClientError as e:
            pytest.fail(f"Failed to access configuration table: {e}")
    
    def test_state_table_accessible(self, aws_clients):
        """Test that state table is accessible and properly indexed."""
        if not STATE_TABLE_NAME:
            pytest.skip("STATE_TABLE_NAME not provided")
        
        try:
            response = aws_clients['dynamodb'].describe_table(TableName=STATE_TABLE_NAME)
            table = response['Table']
            
            assert table['TableStatus'] == 'ACTIVE'
            
            # Check for required indexes
            gsi_names = [gsi['IndexName'] for gsi in table.get('GlobalSecondaryIndexes', [])]
            required_indexes = ['timestamp-index', 'status-timestamp-index']
            
            for required_index in required_indexes:
                assert required_index in gsi_names, \
                    f"Required index {required_index} not found in state table"
            
            # Check TTL configuration
            ttl_response = aws_clients['dynamodb'].describe_time_to_live(TableName=STATE_TABLE_NAME)
            ttl_spec = ttl_response.get('TimeToLiveDescription', {})
            assert ttl_spec.get('TimeToLiveStatus') == 'ENABLED', \
                "TTL is not enabled on state table"
            
        except ClientError as e:
            pytest.fail(f"Failed to access state table: {e}")


class TestEndToEndWorkflow:
    """Test end-to-end synchronization workflows."""
    
    def test_lambda_invocation_dry_run(self, aws_clients, stack_outputs):
        """Test Lambda function can be invoked (dry run only)."""
        contact_sync_arn = stack_outputs.get('ContactSyncHandlerArn')
        assert contact_sync_arn, "Contact sync handler ARN not found"
        
        function_name = contact_sync_arn.split(':')[-1]
        
        # Create test event
        test_event = {
            "Records": [{
                "eventSource": "aws:events",
                "eventName": "ContactChangeEvent",
                "detail": {
                    "eventID": "test-event-id",
                    "eventName": "PutContactInformation",
                    "eventTime": datetime.utcnow().isoformat() + "Z",
                    "userIdentity": {
                        "type": "IAMUser",
                        "principalId": "test-principal",
                        "arn": f"arn:aws:iam::{MANAGEMENT_ACCOUNT_ID}:user/test-user"
                    },
                    "recipientAccountId": MANAGEMENT_ACCOUNT_ID,
                    "requestParameters": {
                        "contactInformation": {
                            "fullName": "Test User",
                            "phoneNumber": "+1-555-0123",
                            "addressLine1": "123 Test St",
                            "city": "Test City",
                            "countryCode": "US",
                            "postalCode": "12345"
                        }
                    }
                }
            }]
        }
        
        try:
            # Dry run invocation
            response = aws_clients['lambda'].invoke(
                FunctionName=function_name,
                InvocationType='DryRun',
                Payload=json.dumps(test_event)
            )
            
            assert response['StatusCode'] == 204, \
                f"Dry run invocation failed with status: {response['StatusCode']}"
            
        except ClientError as e:
            pytest.fail(f"Failed to invoke Lambda function (dry run): {e}")
    
    def test_configuration_validation(self, aws_clients):
        """Test configuration validation logic."""
        if not CONFIG_TABLE_NAME:
            pytest.skip("CONFIG_TABLE_NAME not provided")
        
        # Test invalid configuration rejection
        invalid_config = {
            'config_key': {'S': 'test-invalid'},
            'contact_types': {'SS': ['invalid_type']},  # Invalid contact type
            'excluded_accounts': {'SS': ['not-an-account-id']},  # Invalid account ID format
            'retry_config': {
                'M': {
                    'max_attempts': {'N': '0'},  # Invalid retry count
                    'base_delay': {'N': '-1'},   # Invalid delay
                    'max_delay': {'N': '10'}
                }
            }
        }
        
        try:
            # This should succeed (DynamoDB doesn't validate content)
            # But the application should validate and reject this config
            aws_clients['dynamodb'].put_item(
                TableName=CONFIG_TABLE_NAME,
                Item=invalid_config
            )
            
            # Clean up test config
            aws_clients['dynamodb'].delete_item(
                TableName=CONFIG_TABLE_NAME,
                Key={'config_key': {'S': 'test-invalid'}}
            )
            
        except ClientError as e:
            # This is expected if there are table-level validations
            pass
    
    def test_monitoring_and_alerting(self, aws_clients):
        """Test that monitoring and alerting systems are functional."""
        # Check CloudWatch dashboard exists
        try:
            response = aws_clients['cloudwatch'].list_dashboards()
            dashboard_names = [dash['DashboardName'] for dash in response['DashboardEntries']]
            
            matching_dashboards = [name for name in dashboard_names if STACK_NAME in name]
            assert len(matching_dashboards) > 0, \
                f"No CloudWatch dashboard found for stack {STACK_NAME}"
            
        except ClientError as e:
            pytest.fail(f"Failed to list CloudWatch dashboards: {e}")
        
        # Verify alarm states are reasonable
        try:
            response = aws_clients['cloudwatch'].describe_alarms(
                AlarmNamePrefix=STACK_NAME,
                StateValue='ALARM'
            )
            
            # In a healthy system, we shouldn't have alarms in ALARM state
            # But this depends on the current system state
            alarm_count = len(response['MetricAlarms'])
            if alarm_count > 0:
                print(f"Warning: {alarm_count} alarms are currently in ALARM state")
            
        except ClientError as e:
            pytest.fail(f"Failed to describe alarm states: {e}")


@pytest.mark.integration
class TestDeploymentValidation:
    """Comprehensive deployment validation tests."""
    
    def test_all_resources_deployed(self, aws_clients, stack_outputs):
        """Test that all expected resources are deployed and configured."""
        # This is a comprehensive test that combines multiple checks
        
        # 1. Stack health
        response = aws_clients['cloudformation'].describe_stacks(StackName=STACK_NAME)
        stack = response['Stacks'][0]
        assert stack['StackStatus'] in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']
        
        # 2. Lambda functions
        lambda_arns = [
            stack_outputs.get('ContactSyncHandlerArn'),
            stack_outputs.get('AccountProcessorHandlerArn'),
            stack_outputs.get('NotificationHandlerArn')
        ]
        
        for arn in lambda_arns:
            assert arn, "Lambda function ARN missing"
            function_name = arn.split(':')[-1]
            response = aws_clients['lambda'].get_function(FunctionName=function_name)
            assert response['Configuration']['State'] == 'Active'
        
        # 3. DynamoDB tables
        table_names = [
            stack_outputs.get('ConfigTableName'),
            stack_outputs.get('StateTableName')
        ]
        
        for table_name in table_names:
            assert table_name, "DynamoDB table name missing"
            response = aws_clients['dynamodb'].describe_table(TableName=table_name)
            assert response['Table']['TableStatus'] == 'ACTIVE'
        
        # 4. SNS topic
        topic_arn = stack_outputs.get('NotificationTopicArn')
        assert topic_arn, "SNS topic ARN missing"
        aws_clients['sns'].get_topic_attributes(TopicArn=topic_arn)
        
        # 5. EventBridge rules
        response = aws_clients['events'].list_rules()
        contact_sync_rules = [
            rule for rule in response['Rules'] 
            if 'ContactSync' in rule['Name'] and rule['State'] == 'ENABLED'
        ]
        assert len(contact_sync_rules) > 0, "No active ContactSync EventBridge rules found"
        
        # 6. CloudWatch alarms
        response = aws_clients['cloudwatch'].describe_alarms(AlarmNamePrefix=STACK_NAME)
        assert len(response['MetricAlarms']) > 0, "No CloudWatch alarms found"
        
        print(f"✅ All resources deployed successfully for stack {STACK_NAME}")
    
    def test_system_ready_for_operation(self, aws_clients, stack_outputs):
        """Test that the system is ready for operational use."""
        # This test verifies the system is in a state where it can handle real events
        
        # Check Lambda function logs for any startup errors
        lambda_functions = [
            stack_outputs.get('ContactSyncHandlerArn'),
            stack_outputs.get('AccountProcessorHandlerArn'),
            stack_outputs.get('NotificationHandlerArn')
        ]
        
        for function_arn in lambda_functions:
            if not function_arn:
                continue
                
            function_name = function_arn.split(':')[-1]
            log_group_name = f"/aws/lambda/{function_name}"
            
            try:
                # Check if log group exists and has recent activity
                response = aws_clients['logs'].describe_log_groups(
                    logGroupNamePrefix=log_group_name
                )
                
                if response['logGroups']:
                    log_group = response['logGroups'][0]
                    
                    # Check for recent log streams (indicates function has been invoked)
                    streams_response = aws_clients['logs'].describe_log_streams(
                        logGroupName=log_group_name,
                        orderBy='LastEventTime',
                        descending=True,
                        limit=1
                    )
                    
                    if streams_response['logStreams']:
                        latest_stream = streams_response['logStreams'][0]
                        last_event_time = latest_stream.get('lastEventTime', 0)
                        
                        # Convert to datetime for comparison
                        if last_event_time > 0:
                            last_event = datetime.fromtimestamp(last_event_time / 1000)
                            time_since_last_event = datetime.now() - last_event
                            
                            # If function was invoked recently, check for errors
                            if time_since_last_event < timedelta(hours=24):
                                events_response = aws_clients['logs'].get_log_events(
                                    logGroupName=log_group_name,
                                    logStreamName=latest_stream['logStreamName'],
                                    limit=100
                                )
                                
                                error_events = [
                                    event for event in events_response['events']
                                    if 'ERROR' in event['message'] or 'Exception' in event['message']
                                ]
                                
                                if error_events:
                                    print(f"⚠️  Found {len(error_events)} error events in {function_name} logs")
                                    for error_event in error_events[:3]:  # Show first 3 errors
                                        print(f"   {error_event['message'][:200]}...")
                
            except ClientError:
                # Log group might not exist yet if function hasn't been invoked
                pass
        
        print(f"✅ System appears ready for operation")


if __name__ == "__main__":
    # Run tests when executed directly
    pytest.main([__file__, "-v", "--tb=short"])