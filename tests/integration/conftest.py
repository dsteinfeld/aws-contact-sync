"""
Configuration and fixtures for integration tests.

This module provides shared fixtures and configuration for integration tests
that require actual AWS resources and deployed infrastructure.
"""

import os
import pytest
import boto3
from botocore.exceptions import ClientError


def pytest_configure(config):
    """Configure pytest for integration tests."""
    # Add custom markers
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (require deployed infrastructure)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow running"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to handle integration test markers."""
    # Skip integration tests if not explicitly requested
    if not config.getoption("--integration"):
        skip_integration = pytest.mark.skip(reason="need --integration option to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)


def pytest_addoption(parser):
    """Add command line options for integration tests."""
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="run integration tests (requires deployed infrastructure)"
    )
    parser.addoption(
        "--stack-name",
        action="store",
        default=None,
        help="CloudFormation stack name for integration tests"
    )
    parser.addoption(
        "--aws-region",
        action="store",
        default="us-east-1",
        help="AWS region for integration tests"
    )


@pytest.fixture(scope="session")
def integration_config(request):
    """Configuration for integration tests."""
    return {
        'stack_name': request.config.getoption("--stack-name") or os.environ.get('STACK_NAME'),
        'aws_region': request.config.getoption("--aws-region") or os.environ.get('AWS_REGION', 'us-east-1'),
        'management_account_id': None,  # Will be determined from STS
    }


@pytest.fixture(scope="session")
def aws_credentials():
    """Verify AWS credentials are available."""
    try:
        sts_client = boto3.client('sts')
        identity = sts_client.get_caller_identity()
        return {
            'account_id': identity['Account'],
            'user_id': identity['UserId'],
            'arn': identity['Arn']
        }
    except Exception as e:
        pytest.skip(f"AWS credentials not available: {e}")


@pytest.fixture(scope="session")
def stack_exists(integration_config):
    """Verify that the CloudFormation stack exists."""
    stack_name = integration_config['stack_name']
    if not stack_name:
        pytest.skip("Stack name not provided (use --stack-name or STACK_NAME env var)")
    
    region = integration_config['aws_region']
    cf_client = boto3.client('cloudformation', region_name=region)
    
    try:
        cf_client.describe_stacks(StackName=stack_name)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ValidationError':
            pytest.skip(f"Stack {stack_name} does not exist in region {region}")
        raise


@pytest.fixture(scope="session")
def cleanup_test_data():
    """Fixture to track and cleanup test data."""
    test_data = {
        'dynamodb_items': [],
        'sns_subscriptions': [],
        'lambda_invocations': []
    }
    
    yield test_data
    
    # Cleanup logic would go here
    # For now, we rely on TTL and manual cleanup


@pytest.fixture
def test_timeout():
    """Default timeout for integration tests."""
    return 30  # seconds


class IntegrationTestHelper:
    """Helper class for integration test utilities."""
    
    def __init__(self, aws_region, stack_name):
        self.aws_region = aws_region
        self.stack_name = stack_name
        self._stack_outputs = None
    
    @property
    def stack_outputs(self):
        """Lazy load stack outputs."""
        if self._stack_outputs is None:
            cf_client = boto3.client('cloudformation', region_name=self.aws_region)
            response = cf_client.describe_stacks(StackName=self.stack_name)
            stack = response['Stacks'][0]
            
            self._stack_outputs = {}
            for output in stack.get('Outputs', []):
                self._stack_outputs[output['OutputKey']] = output['OutputValue']
        
        return self._stack_outputs
    
    def get_lambda_function_name(self, output_key):
        """Get Lambda function name from stack output ARN."""
        arn = self.stack_outputs.get(output_key)
        if arn:
            return arn.split(':')[-1]
        return None
    
    def invoke_lambda_sync(self, function_name, payload):
        """Invoke Lambda function synchronously."""
        lambda_client = boto3.client('lambda', region_name=self.aws_region)
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=payload
        )
        return response
    
    def invoke_lambda_async(self, function_name, payload):
        """Invoke Lambda function asynchronously."""
        lambda_client = boto3.client('lambda', region_name=self.aws_region)
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=payload
        )
        return response
    
    def get_dynamodb_item(self, table_name, key):
        """Get item from DynamoDB table."""
        dynamodb_client = boto3.client('dynamodb', region_name=self.aws_region)
        response = dynamodb_client.get_item(
            TableName=table_name,
            Key=key
        )
        return response.get('Item')
    
    def put_dynamodb_item(self, table_name, item):
        """Put item to DynamoDB table."""
        dynamodb_client = boto3.client('dynamodb', region_name=self.aws_region)
        response = dynamodb_client.put_item(
            TableName=table_name,
            Item=item
        )
        return response
    
    def delete_dynamodb_item(self, table_name, key):
        """Delete item from DynamoDB table."""
        dynamodb_client = boto3.client('dynamodb', region_name=self.aws_region)
        response = dynamodb_client.delete_item(
            TableName=table_name,
            Key=key
        )
        return response


@pytest.fixture
def integration_helper(integration_config, stack_exists):
    """Helper instance for integration tests."""
    return IntegrationTestHelper(
        integration_config['aws_region'],
        integration_config['stack_name']
    )


# Pytest markers for different test categories
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get('STACK_NAME') and not os.environ.get('PYTEST_INTEGRATION'),
        reason="Integration tests require STACK_NAME environment variable or --integration flag"
    )
]