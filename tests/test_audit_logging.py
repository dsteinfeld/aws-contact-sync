"""Property-based tests for comprehensive audit logging.

Feature: aws-contact-sync, Property 5: Comprehensive Audit Logging
Validates: Requirements 4.1, 4.2
"""

import pytest
from datetime import datetime, timedelta, UTC
from hypothesis import given, strategies as st, assume
from src.config.dynamodb_state_tracker import DynamoDBStateTracker
from src.models.contact_models import ContactInformation, AlternateContact
from src.models.sync_models import AccountSyncResult


# Hypothesis strategies for generating test data
aws_account_ids = st.text(alphabet="0123456789", min_size=12, max_size=12)
aws_arns = st.text(min_size=20, max_size=100).map(
    lambda x: f"arn:aws:iam::{x[:12].zfill(12)}:user/{x[12:].replace('/', '-')[:20]}"
)
contact_types = st.sampled_from(["primary", "billing", "operations", "security"])
sync_statuses = st.sampled_from(["pending", "in_progress", "completed", "failed"])
account_sync_statuses = st.sampled_from(["success", "failed", "skipped"])

# Contact information generators with proper validation
contact_info_strategy = st.builds(
    ContactInformation,
    address_line1=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
    city=st.text(min_size=1, max_size=30).filter(lambda x: x.strip()),
    country_code=st.text(min_size=2, max_size=3).filter(lambda x: x.strip()),
    full_name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
    phone_number=st.text(min_size=10, max_size=20).filter(lambda x: x.strip()),
    postal_code=st.text(min_size=1, max_size=10).filter(lambda x: x.strip()),
    address_line2=st.one_of(st.none(), st.text(max_size=50)),
    company_name=st.one_of(st.none(), st.text(max_size=50)),
    state_or_region=st.one_of(st.none(), st.text(max_size=30))
)

alternate_contact_strategy = st.builds(
    AlternateContact,
    contact_type=st.sampled_from(["BILLING", "OPERATIONS", "SECURITY"]),
    email_address=st.text(min_size=1, max_size=40).filter(lambda x: x.strip()).map(lambda x: f"{x}@example.com"),
    name=st.text(min_size=1, max_size=50).filter(lambda x: x.strip()),
    phone_number=st.text(min_size=10, max_size=20).filter(lambda x: x.strip()),
    title=st.text(min_size=1, max_size=30).filter(lambda x: x.strip())
)

contact_data_strategy = st.one_of(contact_info_strategy, alternate_contact_strategy)

# Account sync result generator
account_sync_result_strategy = st.builds(
    AccountSyncResult,
    account_id=aws_account_ids,
    status=account_sync_statuses,
    timestamp=st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2030, 12, 31)),
    error_message=st.one_of(st.none(), st.text(min_size=1, max_size=200)),
    retry_count=st.integers(min_value=0, max_value=5)
)


@pytest.mark.property
class TestComprehensiveAuditLogging:
    """Property 5: Comprehensive Audit Logging
    
    For any synchronization operation (successful or failed), the system should 
    log all required audit information including timestamp, initiating user, 
    source account, target accounts, changed fields, and error details where applicable.
    """

    @given(
        initiating_user=aws_arns,
        contact_type=contact_types,
        source_account=aws_account_ids,
        target_accounts=st.lists(aws_account_ids, min_size=1, max_size=10, unique=True),
        contact_data=contact_data_strategy
    )
    def test_sync_operation_creation_logs_all_required_fields(
        self, 
        initiating_user, 
        contact_type, 
        source_account, 
        target_accounts, 
        contact_data
    ):
        """Creating a sync operation should log all required audit information."""
        # Create a mock state tracker (not using actual DynamoDB for property tests)
        tracker = DynamoDBStateTracker()
        
        # Mock the DynamoDB operations to avoid actual AWS calls
        original_get_table = tracker._get_table
        
        def mock_get_table():
            class MockTable:
                def put_item(self, Item):
                    # Verify all required audit fields are present
                    required_fields = [
                        'sync_id', 'timestamp', 'initiating_user', 'contact_type',
                        'source_account', 'target_accounts', 'status', 'contact_data',
                        'created_at', 'updated_at', 'ttl'
                    ]
                    
                    for field in required_fields:
                        assert field in Item, f"Required audit field '{field}' missing from logged item"
                    
                    # Verify field values match input
                    assert Item['initiating_user'] == initiating_user
                    assert Item['contact_type'] == contact_type
                    assert Item['source_account'] == source_account
                    assert Item['target_accounts'] == target_accounts
                    assert Item['status'] == 'pending'
                    
                    # Verify timestamps are present and valid
                    assert Item['timestamp'] is not None
                    assert Item['created_at'] is not None
                    assert Item['updated_at'] is not None
                    assert isinstance(Item['ttl'], int)
                    
                    # Verify contact data is serialized
                    assert Item['contact_data'] is not None
                    
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
            
            return MockTable()
        
        tracker._get_table = mock_get_table
        
        # Create sync operation - this should log all required audit information
        sync_operation = tracker.create_sync_operation(
            initiating_user=initiating_user,
            contact_type=contact_type,
            source_account=source_account,
            target_accounts=target_accounts,
            contact_data=contact_data
        )
        
        # Verify the returned operation contains all audit information
        assert sync_operation.sync_id is not None
        assert sync_operation.timestamp is not None
        assert sync_operation.initiating_user == initiating_user
        assert sync_operation.contact_type == contact_type
        assert sync_operation.source_account == source_account
        assert sync_operation.target_accounts == target_accounts
        assert sync_operation.status == "pending"
        assert sync_operation.contact_data == contact_data

    @given(
        sync_id=st.text(min_size=10, max_size=50),
        account_results=st.lists(account_sync_result_strategy, min_size=1, max_size=5, unique_by=lambda x: x.account_id)
    )
    def test_account_result_logging_includes_error_details(self, sync_id, account_results):
        """Adding account results should log error details when applicable."""
        tracker = DynamoDBStateTracker()
        
        # Mock DynamoDB operations
        stored_results = {}
        
        def mock_get_table():
            class MockTable:
                def get_item(self, Key):
                    return {
                        'Item': {
                            'sync_id': sync_id,
                            'results': '{}',  # Start with empty results
                            'timestamp': datetime.now(UTC).isoformat()
                        }
                    }
                
                def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
                    # Capture the results being stored
                    import json
                    results_json = ExpressionAttributeValues[':results']
                    stored_results.update(json.loads(results_json))
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
            
            return MockTable()
        
        tracker._get_table = mock_get_table
        
        # Add each account result
        for result in account_results:
            success = tracker.add_account_result(sync_id, result)
            assert success is True
        
        # Verify all required audit information is logged for each account
        for result in account_results:
            account_id = result.account_id
            assert account_id in stored_results
            
            logged_result = stored_results[account_id]
            
            # Verify all required fields are present
            required_fields = ['account_id', 'status', 'timestamp', 'error_message', 'retry_count']
            for field in required_fields:
                assert field in logged_result, f"Required field '{field}' missing from account result"
            
            # Verify field values match input
            assert logged_result['account_id'] == result.account_id
            assert logged_result['status'] == result.status
            assert logged_result['retry_count'] == result.retry_count
            
            # Verify error details are logged when applicable
            if result.status == 'failed':
                if result.error_message is not None:
                    assert logged_result['error_message'] == result.error_message
                else:
                    # Error message should be present for failed operations, even if None
                    assert 'error_message' in logged_result

    @given(
        operations_data=st.lists(
            st.tuples(
                aws_arns,  # initiating_user
                contact_types,  # contact_type
                aws_account_ids,  # source_account
                st.lists(aws_account_ids, min_size=1, max_size=3, unique=True),  # target_accounts
                contact_data_strategy,  # contact_data
                sync_statuses  # final_status
            ),
            min_size=1,
            max_size=5
        )
    )
    def test_audit_trail_maintains_operation_history(self, operations_data):
        """The audit trail should maintain complete history of all operations."""
        tracker = DynamoDBStateTracker()
        
        # Mock storage for audit trail
        audit_trail = []
        
        def mock_get_table():
            class MockTable:
                def put_item(self, Item):
                    # Store audit record
                    audit_trail.append(Item.copy())
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
                
                def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
                    # Find and update existing record
                    for record in audit_trail:
                        if record.get('sync_id') == Key['sync_id']:
                            if ':status' in ExpressionAttributeValues:
                                record['status'] = ExpressionAttributeValues[':status']
                            if ':updated' in ExpressionAttributeValues:
                                record['updated_at'] = ExpressionAttributeValues[':updated']
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
                
                def scan(self, **kwargs):
                    # Return all audit records for history queries
                    return {'Items': audit_trail}
            
            return MockTable()
        
        tracker._get_table = mock_get_table
        
        created_operations = []
        
        # Create multiple sync operations
        for initiating_user, contact_type, source_account, target_accounts, contact_data, final_status in operations_data:
            operation = tracker.create_sync_operation(
                initiating_user=initiating_user,
                contact_type=contact_type,
                source_account=source_account,
                target_accounts=target_accounts,
                contact_data=contact_data
            )
            created_operations.append((operation, final_status))
            
            # Update status to final status
            tracker.update_sync_status(operation.sync_id, final_status)
        
        # Query audit history
        history = tracker.query_sync_history(limit=100)
        
        # Verify all operations are in the audit trail
        assert len(history) == len(operations_data)
        
        # Verify each operation maintains complete audit information
        for i, (original_operation, expected_status) in enumerate(created_operations):
            # Find corresponding history record
            history_record = next(
                (h for h in history if h.sync_id == original_operation.sync_id), 
                None
            )
            
            assert history_record is not None, f"Operation {original_operation.sync_id} not found in audit trail"
            
            # Verify all audit fields are preserved
            assert history_record.sync_id == original_operation.sync_id
            assert history_record.initiating_user == original_operation.initiating_user
            assert history_record.contact_type == original_operation.contact_type
            assert history_record.source_account == original_operation.source_account
            assert history_record.target_accounts == original_operation.target_accounts
            assert history_record.status == expected_status
            assert history_record.contact_data == original_operation.contact_data

    @given(
        days_back=st.integers(min_value=1, max_value=90),
        num_operations=st.integers(min_value=1, max_value=20)
    )
    def test_audit_retention_respects_90_day_policy(self, days_back, num_operations):
        """Audit logs should be retained for at least 90 days with proper TTL."""
        tracker = DynamoDBStateTracker()
        
        # Track TTL values set during operation creation
        ttl_values = []
        
        def mock_get_table():
            class MockTable:
                def put_item(self, Item):
                    # Capture TTL value
                    if 'ttl' in Item:
                        ttl_values.append(Item['ttl'])
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
            
            return MockTable()
        
        tracker._get_table = mock_get_table
        
        # Create operations
        for _ in range(num_operations):
            tracker.create_sync_operation(
                initiating_user="arn:aws:iam::123456789012:user/test",
                contact_type="primary",
                source_account="123456789012",
                target_accounts=["987654321098"],
                contact_data=ContactInformation(
                    address_line1="123 Test St",
                    city="Test City",
                    country_code="US",
                    full_name="Test User",
                    phone_number="555-0123",
                    postal_code="12345"
                )
            )
        
        # Verify TTL is set for 90+ day retention
        current_time = datetime.now(UTC).timestamp()
        ninety_days_from_now = int(current_time + (90 * 24 * 60 * 60))
        
        for ttl in ttl_values:
            assert ttl >= ninety_days_from_now, (
                f"TTL {ttl} should be at least 90 days from now ({ninety_days_from_now})"
            )

    def test_audit_logging_handles_serialization_of_different_contact_types(self):
        """Audit logging should properly serialize both ContactInformation and AlternateContact."""
        tracker = DynamoDBStateTracker()
        
        serialized_data = []
        
        def mock_get_table():
            class MockTable:
                def put_item(self, Item):
                    # Capture serialized contact data
                    import json
                    contact_data = json.loads(Item['contact_data'])
                    serialized_data.append(contact_data)
                    return {'ResponseMetadata': {'HTTPStatusCode': 200}}
            
            return MockTable()
        
        tracker._get_table = mock_get_table
        
        # Test with ContactInformation
        contact_info = ContactInformation(
            address_line1="123 Test St",
            city="Test City",
            country_code="US",
            full_name="Test User",
            phone_number="555-0123",
            postal_code="12345"
        )
        
        tracker.create_sync_operation(
            initiating_user="arn:aws:iam::123456789012:user/test",
            contact_type="primary",
            source_account="123456789012",
            target_accounts=["987654321098"],
            contact_data=contact_info
        )
        
        # Test with AlternateContact
        alternate_contact = AlternateContact(
            contact_type="BILLING",
            email_address="billing@example.com",
            name="Billing Contact",
            phone_number="555-0124",
            title="Billing Manager"
        )
        
        tracker.create_sync_operation(
            initiating_user="arn:aws:iam::123456789012:user/test",
            contact_type="billing",
            source_account="123456789012",
            target_accounts=["987654321098"],
            contact_data=alternate_contact
        )
        
        # Verify both contact types are properly serialized
        assert len(serialized_data) == 2
        
        # Verify ContactInformation serialization
        contact_info_serialized = serialized_data[0]
        assert contact_info_serialized['type'] == 'ContactInformation'
        assert 'data' in contact_info_serialized
        assert contact_info_serialized['data']['full_name'] == "Test User"
        
        # Verify AlternateContact serialization
        alternate_contact_serialized = serialized_data[1]
        assert alternate_contact_serialized['type'] == 'AlternateContact'
        assert 'data' in alternate_contact_serialized
        assert alternate_contact_serialized['data']['name'] == "Billing Contact"