"""DynamoDB-based configuration manager for AWS Contact Sync."""

import json
import boto3
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from botocore.exceptions import ClientError, BotoCoreError
from .config_manager import SyncConfig, ConfigManager


class DynamoDBConfigManager(ConfigManager):
    """DynamoDB-backed configuration manager with CRUD operations."""
    
    def __init__(self, table_name: str = "aws-contact-sync-config", region: str = "us-east-1"):
        """Initialize DynamoDB configuration manager.
        
        Args:
            table_name: Name of the DynamoDB table for configuration storage
            region: AWS region for DynamoDB table
        """
        super().__init__()
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
    
    def create_config(self, config_data: Dict[str, Any]) -> SyncConfig:
        """Create new configuration in DynamoDB.
        
        Args:
            config_data: Configuration dictionary to validate and store
            
        Returns:
            SyncConfig: Validated configuration object
            
        Raises:
            ValueError: If configuration is invalid
            ClientError: If DynamoDB operation fails
        """
        # Validate configuration first
        config = SyncConfig.from_dict(config_data)
        
        try:
            table = self._get_table()
            
            # Store configuration with metadata
            item = {
                'config_id': 'current',  # Single configuration approach
                'config_data': json.dumps(config.to_dict()),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'version': 1
            }
            
            # Use condition to prevent overwriting existing config
            table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(config_id)'
            )
            
            self._config = config
            return config
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise ValueError("Configuration already exists. Use update_config instead.")
            raise ClientError(f"Failed to create configuration: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to create configuration: {e}")
    
    def read_config(self) -> Optional[SyncConfig]:
        """Read configuration from DynamoDB.
        
        Returns:
            SyncConfig: Current configuration or None if not found
            
        Raises:
            ClientError: If DynamoDB operation fails
            ValueError: If stored configuration is invalid
        """
        try:
            table = self._get_table()
            
            response = table.get_item(Key={'config_id': 'current'})
            
            if 'Item' not in response:
                return None
            
            item = response['Item']
            config_data = json.loads(item['config_data'])
            
            # Validate and load configuration
            config = SyncConfig.from_dict(config_data)
            self._config = config
            return config
            
        except ClientError as e:
            raise ClientError(f"Failed to read configuration: {e}")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid configuration data in storage: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to read configuration: {e}")
    
    def update_config(self, updates: Dict[str, Any]) -> SyncConfig:
        """Update existing configuration in DynamoDB.
        
        Args:
            updates: Dictionary of configuration updates to apply
            
        Returns:
            SyncConfig: Updated configuration object
            
        Raises:
            ValueError: If configuration doesn't exist or updates are invalid
            ClientError: If DynamoDB operation fails
        """
        try:
            # First read current configuration
            current_config = self.read_config()
            if current_config is None:
                raise ValueError("No configuration exists. Use create_config instead.")
            
            # Merge updates with current configuration
            current_dict = current_config.to_dict()
            
            # Handle nested updates for retry_config and notification_settings
            if 'retry_config' in updates:
                current_dict['retry_config'].update(updates['retry_config'])
                updates = {k: v for k, v in updates.items() if k != 'retry_config'}
            
            if 'notification_settings' in updates:
                current_dict['notification_settings'].update(updates['notification_settings'])
                updates = {k: v for k, v in updates.items() if k != 'notification_settings'}
            
            current_dict.update(updates)
            
            # Validate merged configuration
            updated_config = SyncConfig.from_dict(current_dict)
            
            # Update in DynamoDB
            table = self._get_table()
            
            table.update_item(
                Key={'config_id': 'current'},
                UpdateExpression='SET config_data = :config, updated_at = :updated, version = version + :inc',
                ExpressionAttributeValues={
                    ':config': json.dumps(updated_config.to_dict()),
                    ':updated': datetime.now(timezone.utc).isoformat(),
                    ':inc': 1
                },
                ConditionExpression='attribute_exists(config_id)'
            )
            
            self._config = updated_config
            return updated_config
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise ValueError("Configuration does not exist. Use create_config instead.")
            raise ClientError(f"Failed to update configuration: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to update configuration: {e}")
    
    def delete_config(self) -> bool:
        """Delete configuration from DynamoDB.
        
        Returns:
            bool: True if configuration was deleted, False if it didn't exist
            
        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            table = self._get_table()
            
            response = table.delete_item(
                Key={'config_id': 'current'},
                ReturnValues='ALL_OLD'
            )
            
            self._config = None
            return 'Attributes' in response
            
        except ClientError as e:
            raise ClientError(f"Failed to delete configuration: {e}")
        except (BotoCoreError, Exception) as e:
            raise RuntimeError(f"Failed to delete configuration: {e}")
    
    def get_contact_type_filter(self) -> List[str]:
        """Get list of contact types to synchronize based on current configuration.
        
        Returns:
            List[str]: Contact types to synchronize
            
        Raises:
            ValueError: If no configuration is loaded
        """
        if self._config is None:
            current_config = self.read_config()
            if current_config is None:
                raise ValueError("No configuration available")
        
        return self._config.contact_types
    
    def get_excluded_accounts(self) -> List[str]:
        """Get list of accounts to exclude from synchronization.
        
        Returns:
            List[str]: Account IDs to exclude
            
        Raises:
            ValueError: If no configuration is loaded
        """
        if self._config is None:
            current_config = self.read_config()
            if current_config is None:
                raise ValueError("No configuration available")
        
        return self._config.excluded_accounts
    
    def is_account_excluded(self, account_id: str) -> bool:
        """Check if an account should be excluded from synchronization.
        
        Args:
            account_id: AWS account ID to check
            
        Returns:
            bool: True if account should be excluded
        """
        try:
            excluded_accounts = self.get_excluded_accounts()
            return account_id in excluded_accounts
        except ValueError:
            # If no configuration, don't exclude any accounts
            return False
    
    def should_sync_contact_type(self, contact_type: str) -> bool:
        """Check if a contact type should be synchronized.
        
        Args:
            contact_type: Contact type to check (primary, billing, operations, security)
            
        Returns:
            bool: True if contact type should be synchronized
        """
        try:
            contact_types = self.get_contact_type_filter()
            return contact_type in contact_types
        except ValueError:
            # If no configuration, sync all contact types by default
            return True