"""DynamoDB-based state tracker for AWS Contact Sync operations."""

import json
import uuid
import boto3
from datetime import datetime, timedelta, UTC
from typing import Dict, List, Optional, Union
from botocore.exceptions import ClientError, BotoCoreError
from ..models.sync_models import SyncOperation, AccountSyncResult
from ..models.contact_models import ContactInformation, AlternateContact


class DynamoDBStateTracker:
    """DynamoDB-backed state tracker for sync operations with audit trail."""
    
    def __init__(self, table_name: str = "aws-contact-sync-state", region: str = "us-east-1"):
        """Initialize DynamoDB state tracker.
        
        Args:
            table_name: Name of the DynamoDB table for state storage
            region: AWS region for DynamoDB table
        """
        self.table_name = table_name
        self.region = region
        self._dynamodb = None
        self._table = None
        
    def _get_table(self):
        """Get DynamoDB table resource with lazy initialization."""
        if self._table is None:
            if self._dynamodb is None:
                self._dynamodb = boto3.resource('dynamodb', region_name=self.region)
            self._table = self._dynamodb.Table(self.table_name)
        return self._table
    
    def _serialize_contact_data(self, contact_data: Union[ContactInformation, AlternateContact]) -> Dict:
        """Serialize contact data for storage."""
        if isinstance(contact_data, ContactInformation):
            return {
                'type': 'ContactInformation',
                'data': {
                    'address_line1': contact_data.address_line1,
                    'address_line2': contact_data.address_line2,
                    'address_line3': contact_data.address_line3,
                    'city': contact_data.city,
                    'company_name': contact_data.company_name,
                    'country_code': contact_data.country_code,
                    'district_or_county': contact_data.district_or_county,
                    'full_name': contact_data.full_name,
                    'phone_number': contact_data.phone_number,
                    'postal_code': contact_data.postal_code,
                    'state_or_region': contact_data.state_or_region,
                    'website_url': contact_data.website_url
                }
            }
        elif isinstance(contact_data, AlternateContact):
            return {
                'type': 'AlternateContact',
                'data': {
                    'contact_type': contact_data.contact_type,
                    'email_address': contact_data.email_address,
                    'name': contact_data.name,
                    'phone_number': contact_data.phone_number,
                    'title': contact_data.title
                }
            }
        else:
            raise ValueError(f"Unsupported contact data type: {type(contact_data)}")
    
    def _deserialize_contact_data(self, serialized: Dict) -> Union[ContactInformation, AlternateContact]:
        """Deserialize contact data from storage."""
        contact_type = serialized['type']
        data = serialized['data']
        
        if contact_type == 'ContactInformation':
            return ContactInformation(**data)
        elif contact_type == 'AlternateContact':
            return AlternateContact(**data)
        else:
            raise ValueError(f"Unknown contact data type: {contact_type}")
    
    def create_sync_operation(
        self,
        initiating_user: str,
        contact_type: str,
        source_account: str,
        target_accounts: List[str],
        contact_data: Union[ContactInformation, AlternateContact]
    ) -> SyncOperation:
        """Create a new sync operation record.
        
        Args:
            initiating_user: ARN of user who initiated the sync
            contact_type: Type of contact being synchronized
            source_account: Source account ID (management account)
            target_accounts: List of target account IDs
            contact_data: Contact information being synchronized
            
        Returns:
            SyncOperation: Created sync operation
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        sync_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC)
        
        sync_operation = SyncOperation(
            sync_id=sync_id,
            timestamp=timestamp,
            initiating_user=initiating_user,
            contact_type=contact_type,
            source_account=source_account,
            target_accounts=target_accounts,
            status="pending",
            contact_data=contact_data,
            results={}
        )
        
        try:
            table = self._get_table()
            
            # Calculate TTL for 90-day retention
            ttl_timestamp = int((timestamp + timedelta(days=90)).timestamp())
            
            item = {
                'sync_id': sync_id,
                'timestamp': timestamp.isoformat(),
                'initiating_user': initiating_user,
                'contact_type': contact_type,
                'source_account': source_account,
                'target_accounts': target_accounts,
                'status': 'pending',
                'contact_data': json.dumps(self._serialize_contact_data(contact_data)),
                'results': json.dumps({}),
                'created_at': timestamp.isoformat(),
                'updated_at': timestamp.isoformat(),
                'ttl': ttl_timestamp  # DynamoDB TTL for automatic cleanup
            }
            
            table.put_item(Item=item)
            
            return sync_operation
            
        except (ClientError, BotoCoreError) as e:
            raise ClientError(f"Failed to create sync operation: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to create sync operation: {e}")
    
    def update_sync_status(self, sync_id: str, status: str) -> bool:
        """Update the overall status of a sync operation.
        
        Args:
            sync_id: Unique identifier of the sync operation
            status: New status (pending, in_progress, completed, failed)
            
        Returns:
            bool: True if update was successful
            
        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If sync operation doesn't exist
        """
        try:
            table = self._get_table()
            
            response = table.update_item(
                Key={'sync_id': sync_id},
                UpdateExpression='SET #status = :status, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': status,
                    ':updated': datetime.now(UTC).isoformat()
                },
                ConditionExpression='attribute_exists(sync_id)',
                ReturnValues='UPDATED_NEW'
            )
            
            return 'Attributes' in response
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise ValueError(f"Sync operation {sync_id} does not exist")
            raise ClientError(f"Failed to update sync status: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to update sync status: {e}")
    
    def add_account_result(self, sync_id: str, result: AccountSyncResult) -> bool:
        """Add or update result for a specific account in a sync operation.
        
        Args:
            sync_id: Unique identifier of the sync operation
            result: Account sync result to add
            
        Returns:
            bool: True if update was successful
            
        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If sync operation doesn't exist
        """
        try:
            table = self._get_table()
            
            # First get current results
            response = table.get_item(Key={'sync_id': sync_id})
            
            if 'Item' not in response:
                raise ValueError(f"Sync operation {sync_id} does not exist")
            
            current_results = json.loads(response['Item'].get('results', '{}'))
            
            # Add new result
            current_results[result.account_id] = {
                'account_id': result.account_id,
                'status': result.status,
                'timestamp': result.timestamp.isoformat(),
                'error_message': result.error_message,
                'retry_count': result.retry_count
            }
            
            # Update the record
            table.update_item(
                Key={'sync_id': sync_id},
                UpdateExpression='SET results = :results, updated_at = :updated',
                ExpressionAttributeValues={
                    ':results': json.dumps(current_results),
                    ':updated': datetime.now(UTC).isoformat()
                }
            )
            
            return True
            
        except ClientError as e:
            raise ClientError(f"Failed to add account result: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to add account result: {e}")
    
    def get_sync_operation(self, sync_id: str) -> Optional[SyncOperation]:
        """Retrieve a sync operation by ID.
        
        Args:
            sync_id: Unique identifier of the sync operation
            
        Returns:
            SyncOperation: Sync operation or None if not found
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            table = self._get_table()
            
            response = table.get_item(Key={'sync_id': sync_id})
            
            if 'Item' not in response:
                return None
            
            item = response['Item']
            
            # Deserialize contact data
            contact_data = self._deserialize_contact_data(json.loads(item['contact_data']))
            
            # Deserialize results
            results_data = json.loads(item.get('results', '{}'))
            results = {}
            for account_id, result_data in results_data.items():
                results[account_id] = AccountSyncResult(
                    account_id=result_data['account_id'],
                    status=result_data['status'],
                    timestamp=datetime.fromisoformat(result_data['timestamp']),
                    error_message=result_data.get('error_message'),
                    retry_count=result_data.get('retry_count', 0)
                )
            
            return SyncOperation(
                sync_id=item['sync_id'],
                timestamp=datetime.fromisoformat(item['timestamp']),
                initiating_user=item['initiating_user'],
                contact_type=item['contact_type'],
                source_account=item['source_account'],
                target_accounts=item['target_accounts'],
                status=item['status'],
                contact_data=contact_data,
                results=results
            )
            
        except ClientError as e:
            raise ClientError(f"Failed to get sync operation: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to get sync operation: {e}")
    
    def query_sync_history(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[SyncOperation]:
        """Query sync operation history with optional filters.
        
        Args:
            start_time: Filter operations after this time
            end_time: Filter operations before this time
            status: Filter by operation status
            limit: Maximum number of operations to return
            
        Returns:
            List[SyncOperation]: List of sync operations matching criteria
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            table = self._get_table()
            
            # Build scan parameters
            scan_kwargs = {'Limit': limit}
            filter_expressions = []
            expression_values = {}
            
            if start_time:
                filter_expressions.append('#timestamp >= :start_time')
                expression_values[':start_time'] = start_time.isoformat()
                scan_kwargs['ExpressionAttributeNames'] = {'#timestamp': 'timestamp'}
            
            if end_time:
                filter_expressions.append('#timestamp <= :end_time')
                expression_values[':end_time'] = end_time.isoformat()
                scan_kwargs['ExpressionAttributeNames'] = {'#timestamp': 'timestamp'}
            
            if status:
                filter_expressions.append('#status = :status')
                expression_values[':status'] = status
                if 'ExpressionAttributeNames' not in scan_kwargs:
                    scan_kwargs['ExpressionAttributeNames'] = {}
                scan_kwargs['ExpressionAttributeNames']['#status'] = 'status'
            
            if filter_expressions:
                scan_kwargs['FilterExpression'] = ' AND '.join(filter_expressions)
                scan_kwargs['ExpressionAttributeValues'] = expression_values
            
            response = table.scan(**scan_kwargs)
            
            operations = []
            for item in response.get('Items', []):
                # Deserialize each operation
                contact_data = self._deserialize_contact_data(json.loads(item['contact_data']))
                
                results_data = json.loads(item.get('results', '{}'))
                results = {}
                for account_id, result_data in results_data.items():
                    results[account_id] = AccountSyncResult(
                        account_id=result_data['account_id'],
                        status=result_data['status'],
                        timestamp=datetime.fromisoformat(result_data['timestamp']),
                        error_message=result_data.get('error_message'),
                        retry_count=result_data.get('retry_count', 0)
                    )
                
                operation = SyncOperation(
                    sync_id=item['sync_id'],
                    timestamp=datetime.fromisoformat(item['timestamp']),
                    initiating_user=item['initiating_user'],
                    contact_type=item['contact_type'],
                    source_account=item['source_account'],
                    target_accounts=item['target_accounts'],
                    status=item['status'],
                    contact_data=contact_data,
                    results=results
                )
                operations.append(operation)
            
            # Sort by timestamp descending (most recent first)
            operations.sort(key=lambda x: x.timestamp, reverse=True)
            
            return operations
            
        except ClientError as e:
            raise ClientError(f"Failed to query sync history: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to query sync history: {e}")
    
    def get_sync_statistics(self, days: int = 30) -> Dict[str, int]:
        """Get synchronization statistics for the specified number of days.
        
        Args:
            days: Number of days to look back for statistics
            
        Returns:
            Dict[str, int]: Statistics including total, successful, failed operations
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        start_time = datetime.now(UTC) - timedelta(days=days)
        
        try:
            operations = self.query_sync_history(start_time=start_time, limit=1000)
            
            stats = {
                'total_operations': len(operations),
                'completed_operations': 0,
                'failed_operations': 0,
                'in_progress_operations': 0,
                'pending_operations': 0,
                'total_accounts_processed': 0,
                'successful_account_updates': 0,
                'failed_account_updates': 0
            }
            
            for operation in operations:
                if operation.status == 'completed':
                    stats['completed_operations'] += 1
                elif operation.status == 'failed':
                    stats['failed_operations'] += 1
                elif operation.status == 'in_progress':
                    stats['in_progress_operations'] += 1
                elif operation.status == 'pending':
                    stats['pending_operations'] += 1
                
                # Count account-level results
                for result in operation.results.values():
                    stats['total_accounts_processed'] += 1
                    if result.status == 'success':
                        stats['successful_account_updates'] += 1
                    elif result.status == 'failed':
                        stats['failed_account_updates'] += 1
            
            return stats
            
        except Exception as e:
            raise RuntimeError(f"Failed to get sync statistics: {e}")