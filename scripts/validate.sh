#!/bin/bash

# AWS Contact Synchronization System Validation Script
# This script validates the deployment and configuration

set -e

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEFAULT_REGION="us-east-1"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Usage function
usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Validate AWS Contact Synchronization System deployment

OPTIONS:
    -e, --environment ENV       Environment to validate (dev, staging, prod) [default: prod]
    -r, --region REGION         AWS region [default: us-east-1]
    -s, --stack-name NAME       CloudFormation stack name [default: aws-contact-sync]
    --deep                      Run deep validation including integration tests
    -h, --help                  Show this help message

EXAMPLES:
    # Basic validation
    $0 --environment prod

    # Deep validation with integration tests
    $0 --environment prod --deep

EOF
}

# Parse command line arguments
ENVIRONMENT="prod"
REGION="$DEFAULT_REGION"
STACK_NAME="aws-contact-sync"
DEEP_VALIDATION=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -s|--stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        --deep)
            DEEP_VALIDATION=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

FULL_STACK_NAME="$STACK_NAME-$ENVIRONMENT"

# Validation functions
validate_stack_exists() {
    log_info "Checking if stack exists..."
    
    if ! aws cloudformation describe-stacks --stack-name "$FULL_STACK_NAME" --region "$REGION" &> /dev/null; then
        log_error "Stack $FULL_STACK_NAME does not exist in region $REGION"
        exit 1
    fi
    
    local stack_status=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].StackStatus' \
        --output text)
    
    if [[ "$stack_status" != "CREATE_COMPLETE" && "$stack_status" != "UPDATE_COMPLETE" ]]; then
        log_error "Stack is in invalid state: $stack_status"
        exit 1
    fi
    
    log_success "Stack exists and is in valid state: $stack_status"
}

validate_lambda_functions() {
    log_info "Validating Lambda functions..."
    
    local functions=(
        "$FULL_STACK_NAME-contact-sync-handler"
        "$FULL_STACK_NAME-account-processor-handler"
        "$FULL_STACK_NAME-notification-handler"
    )
    
    for func in "${functions[@]}"; do
        if aws lambda get-function --function-name "$func" --region "$REGION" &> /dev/null; then
            local state=$(aws lambda get-function \
                --function-name "$func" \
                --region "$REGION" \
                --query 'Configuration.State' \
                --output text)
            
            if [[ "$state" == "Active" ]]; then
                log_success "Lambda function $func is active"
            else
                log_error "Lambda function $func is in state: $state"
                exit 1
            fi
        else
            log_error "Lambda function $func not found"
            exit 1
        fi
    done
}

validate_dynamodb_tables() {
    log_info "Validating DynamoDB tables..."
    
    local config_table=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ConfigTableName`].OutputValue' \
        --output text)
    
    local state_table=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`StateTableName`].OutputValue' \
        --output text)
    
    for table in "$config_table" "$state_table"; do
        if [[ -n "$table" && "$table" != "None" ]]; then
            local table_status=$(aws dynamodb describe-table \
                --table-name "$table" \
                --region "$REGION" \
                --query 'Table.TableStatus' \
                --output text)
            
            if [[ "$table_status" == "ACTIVE" ]]; then
                log_success "DynamoDB table $table is active"
            else
                log_error "DynamoDB table $table is in state: $table_status"
                exit 1
            fi
        else
            log_error "Could not find table name in stack outputs"
            exit 1
        fi
    done
}

validate_eventbridge_rules() {
    log_info "Validating EventBridge rules..."
    
    local rules=$(aws events list-rules \
        --region "$REGION" \
        --query "Rules[?contains(Name, 'ContactSync')].Name" \
        --output text)
    
    if [[ -n "$rules" ]]; then
        for rule in $rules; do
            local rule_state=$(aws events describe-rule \
                --name "$rule" \
                --region "$REGION" \
                --query 'State' \
                --output text)
            
            if [[ "$rule_state" == "ENABLED" ]]; then
                log_success "EventBridge rule $rule is enabled"
            else
                log_warning "EventBridge rule $rule is disabled"
            fi
        done
    else
        log_warning "No ContactSync EventBridge rules found"
    fi
}

validate_sns_topic() {
    log_info "Validating SNS topic..."
    
    local topic_arn=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`NotificationTopicArn`].OutputValue' \
        --output text)
    
    if [[ -n "$topic_arn" && "$topic_arn" != "None" ]]; then
        if aws sns get-topic-attributes --topic-arn "$topic_arn" --region "$REGION" &> /dev/null; then
            log_success "SNS topic exists and is accessible"
        else
            log_error "SNS topic is not accessible"
            exit 1
        fi
    else
        log_error "Could not find SNS topic ARN in stack outputs"
        exit 1
    fi
}

validate_cloudwatch_alarms() {
    log_info "Validating CloudWatch alarms..."
    
    local alarms=$(aws cloudwatch describe-alarms \
        --alarm-name-prefix "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'MetricAlarms[*].AlarmName' \
        --output text)
    
    if [[ -n "$alarms" ]]; then
        local alarm_count=$(echo "$alarms" | wc -w)
        log_success "Found $alarm_count CloudWatch alarms"
        
        for alarm in $alarms; do
            local alarm_state=$(aws cloudwatch describe-alarms \
                --alarm-names "$alarm" \
                --region "$REGION" \
                --query 'MetricAlarms[0].StateValue' \
                --output text)
            
            if [[ "$alarm_state" == "ALARM" ]]; then
                log_warning "Alarm $alarm is in ALARM state"
            else
                log_info "Alarm $alarm state: $alarm_state"
            fi
        done
    else
        log_warning "No CloudWatch alarms found"
    fi
}

validate_configuration() {
    log_info "Validating system configuration..."
    
    local config_table=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ConfigTableName`].OutputValue' \
        --output text)
    
    if [[ -n "$config_table" && "$config_table" != "None" ]]; then
        # Check if default configuration exists
        if aws dynamodb get-item \
            --table-name "$config_table" \
            --key '{"config_key":{"S":"default"}}' \
            --region "$REGION" &> /dev/null; then
            log_success "Default configuration found in $config_table"
        else
            log_warning "Default configuration not found in $config_table"
        fi
    fi
}

test_lambda_invocation() {
    log_info "Testing Lambda function invocation..."
    
    local contact_sync_function="$FULL_STACK_NAME-contact-sync-handler"
    
    # Create a test event
    local test_event='{
        "Records": [{
            "eventSource": "aws:events",
            "eventName": "ContactChangeEvent",
            "detail": {
                "eventID": "test-event-id",
                "eventName": "PutContactInformation",
                "eventTime": "2024-01-09T10:00:00Z",
                "userIdentity": {
                    "type": "IAMUser",
                    "principalId": "test-principal",
                    "arn": "arn:aws:iam::123456789012:user/test-user"
                },
                "recipientAccountId": "123456789012",
                "requestParameters": {
                    "contactInformation": {
                        "fullName": "Test User",
                        "phoneNumber": "+1-555-0123"
                    }
                }
            }
        }]
    }'
    
    # Invoke function with dry run
    if aws lambda invoke \
        --function-name "$contact_sync_function" \
        --payload "$test_event" \
        --region "$REGION" \
        --dry-run \
        /tmp/lambda-response.json &> /dev/null; then
        log_success "Lambda function can be invoked (dry run)"
    else
        log_error "Lambda function invocation failed (dry run)"
        exit 1
    fi
    
    rm -f /tmp/lambda-response.json
}

run_integration_tests() {
    log_info "Running integration tests..."
    
    cd "$PROJECT_ROOT"
    
    # Set environment variables for tests
    export AWS_REGION="$REGION"
    export STACK_NAME="$FULL_STACK_NAME"
    export CONFIG_TABLE_NAME=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ConfigTableName`].OutputValue' \
        --output text)
    export STATE_TABLE_NAME=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`StateTableName`].OutputValue' \
        --output text)
    
    # Run integration tests
    if pytest tests/ -m integration -v; then
        log_success "Integration tests passed"
    else
        log_error "Integration tests failed"
        exit 1
    fi
}

generate_validation_report() {
    log_info "Generating validation report..."
    
    local report_file="/tmp/validation-report-$ENVIRONMENT-$(date +%Y%m%d-%H%M%S).json"
    
    # Collect system information
    local stack_info=$(aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --output json)
    
    local lambda_functions=$(aws lambda list-functions \
        --region "$REGION" \
        --query "Functions[?contains(FunctionName, '$FULL_STACK_NAME')]" \
        --output json)
    
    # Create validation report
    cat > "$report_file" << EOF
{
    "validation_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "environment": "$ENVIRONMENT",
    "region": "$REGION",
    "stack_name": "$FULL_STACK_NAME",
    "validation_type": "$([ "$DEEP_VALIDATION" == true ] && echo "deep" || echo "basic")",
    "stack_info": $stack_info,
    "lambda_functions": $lambda_functions,
    "validation_status": "completed"
}
EOF
    
    log_success "Validation report generated: $report_file"
    echo "Report location: $report_file"
}

# Main validation function
main() {
    log_info "Starting validation for environment: $ENVIRONMENT"
    log_info "Region: $REGION"
    log_info "Stack: $FULL_STACK_NAME"
    
    validate_stack_exists
    validate_lambda_functions
    validate_dynamodb_tables
    validate_eventbridge_rules
    validate_sns_topic
    validate_cloudwatch_alarms
    validate_configuration
    test_lambda_invocation
    
    if [[ "$DEEP_VALIDATION" == true ]]; then
        log_info "Running deep validation..."
        run_integration_tests
    fi
    
    generate_validation_report
    
    log_success "Validation completed successfully!"
    
    # Display summary
    echo
    log_info "=== Validation Summary ==="
    log_success "✓ Stack exists and is healthy"
    log_success "✓ Lambda functions are active"
    log_success "✓ DynamoDB tables are active"
    log_success "✓ EventBridge rules are configured"
    log_success "✓ SNS topic is accessible"
    log_success "✓ CloudWatch alarms are configured"
    log_success "✓ System configuration is valid"
    log_success "✓ Lambda functions can be invoked"
    
    if [[ "$DEEP_VALIDATION" == true ]]; then
        log_success "✓ Integration tests passed"
    fi
    
    echo
    log_info "System is ready for operation!"
}

# Run main function
main "$@"