# Technology Stack

## Core Technologies

- **Python 3.8+**: Primary development language
- **AWS Lambda**: Serverless compute for event processing and account updates
- **AWS EventBridge**: Event routing from CloudTrail to Lambda functions
- **AWS CloudTrail**: Contact change event detection
- **AWS DynamoDB**: Configuration storage and sync state tracking
- **AWS Organizations API**: Member account discovery and management
- **AWS Account Management API**: Contact information updates
- **AWS User Notifications**: Primary notification delivery
- **AWS SNS**: Fallback notification mechanism

## Dependencies

### Core Dependencies
- `boto3>=1.34.0`: AWS SDK for Python

### Testing Dependencies
- `pytest>=7.4.0`: Testing framework
- `hypothesis>=6.88.0`: Property-based testing
- `pytest-asyncio>=0.21.0`: Async test support

## Build and Development Commands

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Install with test dependencies
pip install -e .[test]
```

### Testing
```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test types
pytest -m unit          # Unit tests only
pytest -m property      # Property-based tests only
pytest -m integration   # Integration tests only

# Run tests with coverage
pytest --cov=src
```

### Code Quality
```bash
# Format code (if using black)
black src/ tests/

# Lint code (if using flake8)
flake8 src/ tests/

# Type checking (if using mypy)
mypy src/
```

## Architecture Patterns

- **Event-driven serverless**: CloudTrail → EventBridge → Lambda
- **Retry with exponential backoff**: Configurable retry logic for resilience
- **Dataclass-based models**: Type-safe data structures with validation
- **Client wrapper pattern**: AWS API clients with retry logic and error handling
- **Configuration-driven behavior**: DynamoDB-stored configuration for filtering and notifications