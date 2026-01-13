# Project Structure

## Directory Organization

```
src/
├── aws_clients/          # AWS API client wrappers
│   ├── account_management.py    # Account Management API client
│   ├── organizations.py         # Organizations API client
│   └── __init__.py
├── config/               # Configuration management
│   ├── config_manager.py        # Configuration validation and management
│   ├── dynamodb_config_manager.py   # DynamoDB-backed configuration
│   ├── dynamodb_state_tracker.py    # Sync operation state tracking
│   └── __init__.py
├── models/               # Data models and schemas
│   ├── contact_models.py         # Contact information models
│   ├── sync_models.py           # Synchronization operation models
│   └── __init__.py
└── __init__.py

tests/                    # Test suite
├── test_aws_clients.py          # AWS client tests
├── test_configuration_filtering.py  # Configuration filtering tests
├── test_config_validation.py    # Configuration validation tests
├── test_retry_logic.py          # Retry mechanism tests
└── __init__.py
```

## Code Organization Patterns

### AWS Clients (`src/aws_clients/`)
- Wrapper classes around boto3 clients
- Implement retry logic with exponential backoff
- Handle pagination for list operations
- Provide typed return values using dataclasses
- Include comprehensive error handling and logging

### Configuration (`src/config/`)
- `config_manager.py`: In-memory configuration validation and management
- `dynamodb_config_manager.py`: DynamoDB persistence layer for configuration
- `dynamodb_state_tracker.py`: Tracks synchronization operation state and history
- All configuration classes use dataclasses with validation in `__post_init__`

### Models (`src/models/`)
- Dataclass-based models with field validation
- Separate models for different AWS API structures
- `contact_models.py`: ContactInformation and AlternateContact models
- `sync_models.py`: SyncOperation and related tracking models

### Testing (`tests/`)
- Property-based tests using Hypothesis for comprehensive validation
- Unit tests for specific functionality and edge cases
- Integration tests for AWS API interactions
- Test markers: `@pytest.mark.unit`, `@pytest.mark.property`, `@pytest.mark.integration`

## Naming Conventions

- **Files**: Snake_case (e.g., `config_manager.py`)
- **Classes**: PascalCase (e.g., `OrganizationsClient`)
- **Functions/Methods**: Snake_case (e.g., `list_accounts`)
- **Constants**: UPPER_SNAKE_CASE (e.g., `RETRYABLE_ERRORS`)
- **Private methods**: Leading underscore (e.g., `_execute_with_retry`)

## Import Patterns

- Relative imports within the package: `from ..config.config_manager import RetryConfig`
- External dependencies imported at module level
- Type hints imported from `typing` module
- AWS exceptions imported from `botocore.exceptions`