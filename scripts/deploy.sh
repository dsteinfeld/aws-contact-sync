#!/bin/bash

# AWS Contact Synchronization System Deployment Script
# This script automates the deployment of the contact sync system using AWS SAM
# Compatible with Windows (Git Bash), macOS, and Linux

set -e  # Exit on any error

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STACK_NAME="aws-contact-sync"
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

Deploy the AWS Contact Synchronization System

OPTIONS:
    -e, --environment ENV       Deployment environment (dev, staging, prod) [default: prod]
    -r, --region REGION         AWS region [default: us-east-1]
    -a, --account-id ACCOUNT    Management account ID (required)
    -n, --notification-email    Email for notifications (optional)
    -s, --stack-name NAME       CloudFormation stack name [default: aws-contact-sync]
    -p, --profile PROFILE       AWS CLI profile name [default: default]
    --guided                    Use SAM guided deployment
    --no-confirm                Skip deployment confirmation
    --validate-only             Only validate template without deploying
    --rollback                  Rollback to previous version
    -h, --help                  Show this help message

EXAMPLES:
    # Deploy to production with management account ID
    $0 --environment prod --account-id 123456789012 --notification-email admin@company.com

    # Deploy to development environment with specific profile
    $0 --environment dev --account-id 123456789012 --region us-west-2 --profile my-aws-profile

    # Validate template only
    $0 --validate-only --account-id 123456789012

    # Guided deployment
    $0 --guided

EOF
}

# Parse command line arguments
ENVIRONMENT="prod"
REGION="$DEFAULT_REGION"
MANAGEMENT_ACCOUNT_ID=""
NOTIFICATION_EMAIL=""
AWS_PROFILE=""
GUIDED=false
NO_CONFIRM=false
VALIDATE_ONLY=false
ROLLBACK=false

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
        -a|--account-id)
            MANAGEMENT_ACCOUNT_ID="$2"
            shift 2
            ;;
        -n|--notification-email)
            NOTIFICATION_EMAIL="$2"
            shift 2
            ;;
        -s|--stack-name)
            STACK_NAME="$2"
            shift 2
            ;;
        -p|--profile)
            AWS_PROFILE="$2"
            shift 2
            ;;
        --guided)
            GUIDED=true
            shift
            ;;
        --no-confirm)
            NO_CONFIRM=true
            shift
            ;;
        --validate-only)
            VALIDATE_ONLY=true
            shift
            ;;
        --rollback)
            ROLLBACK=true
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

# Set up AWS CLI profile if specified
AWS_CLI_OPTS=()
if [[ -n "$AWS_PROFILE" ]]; then
    AWS_CLI_OPTS+=("--profile" "$AWS_PROFILE")
    export AWS_PROFILE  # Also set as environment variable for SAM CLI
fi

# Validation functions
validate_environment() {
    if [[ ! "$ENVIRONMENT" =~ ^(dev|staging|prod)$ ]]; then
        log_error "Invalid environment: $ENVIRONMENT. Must be dev, staging, or prod."
        exit 1
    fi
}

validate_account_id() {
    if [[ ! "$MANAGEMENT_ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
        log_error "Invalid management account ID: $MANAGEMENT_ACCOUNT_ID. Must be 12 digits."
        exit 1
    fi
}

validate_email() {
    if [[ -n "$NOTIFICATION_EMAIL" && ! "$NOTIFICATION_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
        log_error "Invalid email format: $NOTIFICATION_EMAIL"
        exit 1
    fi
}

validate_aws_cli() {
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws "${AWS_CLI_OPTS[@]}" sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured. Please run 'aws configure' first."
        if [[ -n "$AWS_PROFILE" ]]; then
            log_error "Profile '$AWS_PROFILE' may not exist or is not configured properly."
        fi
        exit 1
    fi
}

validate_sam_cli() {
    if ! command -v sam &> /dev/null; then
        log_error "SAM CLI is not installed. Please install it first."
        exit 1
    fi
}

validate_permissions() {
    log_info "Validating AWS permissions..."
    
    # Check if user has necessary permissions
    local required_permissions=(
        "cloudformation:CreateStack"
        "cloudformation:UpdateStack"
        "cloudformation:DescribeStacks"
        "lambda:CreateFunction"
        "lambda:UpdateFunctionCode"
        "iam:CreateRole"
        "iam:AttachRolePolicy"
        "dynamodb:CreateTable"
        "events:PutRule"
        "sns:CreateTopic"
        "s3:CreateBucket"
        "cloudtrail:CreateTrail"
    )
    
    # This is a simplified check - in practice, you might want more comprehensive validation
    if ! aws "${AWS_CLI_OPTS[@]}" iam get-user &> /dev/null && ! aws "${AWS_CLI_OPTS[@]}" sts get-caller-identity --query 'Arn' --output text | grep -q 'role/'; then
        log_warning "Unable to verify IAM permissions. Proceeding with deployment..."
    fi
}

# Pre-deployment checks
pre_deployment_checks() {
    log_info "Running pre-deployment checks..."
    
    validate_aws_cli
    validate_sam_cli
    validate_environment
    
    if [[ "$GUIDED" == false && "$VALIDATE_ONLY" == false ]]; then
        if [[ -z "$MANAGEMENT_ACCOUNT_ID" ]]; then
            log_error "Management account ID is required for non-guided deployment"
            exit 1
        fi
        validate_account_id
    fi
    
    if [[ -n "$NOTIFICATION_EMAIL" ]]; then
        validate_email
    fi
    
    validate_permissions
    
    # Check if we're in the right directory
    if [[ ! -f "$PROJECT_ROOT/template.yaml" ]]; then
        log_error "template.yaml not found. Please run this script from the project root."
        exit 1
    fi
    
    log_success "Pre-deployment checks passed"
}

# Build the SAM application
build_application() {
    log_info "Building SAM application..."
    
    cd "$PROJECT_ROOT"
    
    # Clean previous builds
    if [[ -d ".aws-sam" ]]; then
        rm -rf .aws-sam
    fi
    
    # Build the application
    if ! sam build --cached --parallel; then
        log_error "SAM build failed"
        exit 1
    fi
    
    log_success "Application built successfully"
}

# Validate the SAM template
validate_template() {
    log_info "Validating SAM template..."
    
    cd "$PROJECT_ROOT"
    
    if ! sam validate --lint; then
        log_error "Template validation failed"
        exit 1
    fi
    
    log_success "Template validation passed"
}

# Deploy the application
deploy_application() {
    log_info "Deploying application to $ENVIRONMENT environment..."
    
    cd "$PROJECT_ROOT"
    
    # Prepare deployment parameters
    local deploy_params=(
        "--stack-name" "$STACK_NAME-$ENVIRONMENT"
        "--region" "$REGION"
        "--capabilities" "CAPABILITY_IAM"
        "--capabilities" "CAPABILITY_NAMED_IAM"
        "--parameter-overrides"
        "Environment=$ENVIRONMENT"
        "ManagementAccountId=$MANAGEMENT_ACCOUNT_ID"
    )
    
    # Add optional parameters
    if [[ -n "$NOTIFICATION_EMAIL" ]]; then
        deploy_params+=("NotificationEmail=$NOTIFICATION_EMAIL")
    fi
    
    # Add confirmation flag
    if [[ "$NO_CONFIRM" == false ]]; then
        deploy_params+=("--confirm-changeset")
    fi
    
    # Deploy based on mode
    if [[ "$GUIDED" == true ]]; then
        log_info "Starting guided deployment..."
        sam deploy --guided
    else
        log_info "Starting automated deployment..."
        if ! sam deploy "${deploy_params[@]}" --resolve-s3; then
            log_error "Deployment failed"
            exit 1
        fi
    fi
    
    log_success "Deployment completed successfully"
}

# Get stack outputs
get_stack_outputs() {
    log_info "Retrieving stack outputs..."
    
    local stack_name="$STACK_NAME-$ENVIRONMENT"
    
    if aws "${AWS_CLI_OPTS[@]}" cloudformation describe-stacks --stack-name "$stack_name" --region "$REGION" &> /dev/null; then
        echo
        log_info "Stack Outputs:"
        aws "${AWS_CLI_OPTS[@]}" cloudformation describe-stacks \
            --stack-name "$stack_name" \
            --region "$REGION" \
            --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue,Description]' \
            --output table
        
        # Get dashboard URL
        local dashboard_url=$(aws "${AWS_CLI_OPTS[@]}" cloudformation describe-stacks \
            --stack-name "$stack_name" \
            --region "$REGION" \
            --query 'Stacks[0].Outputs[?OutputKey==`DashboardURL`].OutputValue' \
            --output text)
        
        if [[ -n "$dashboard_url" && "$dashboard_url" != "None" ]]; then
            echo
            log_info "CloudWatch Dashboard: $dashboard_url"
        fi
    else
        log_warning "Could not retrieve stack outputs"
    fi
}

# Rollback function
rollback_deployment() {
    log_info "Rolling back deployment..."
    
    local stack_name="$STACK_NAME-$ENVIRONMENT"
    
    if ! aws "${AWS_CLI_OPTS[@]}" cloudformation describe-stacks --stack-name "$stack_name" --region "$REGION" &> /dev/null; then
        log_error "Stack $stack_name does not exist"
        exit 1
    fi
    
    log_warning "This will rollback the stack to the previous version. Are you sure? (y/N)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        aws "${AWS_CLI_OPTS[@]}" cloudformation cancel-update-stack --stack-name "$stack_name" --region "$REGION" || true
        aws "${AWS_CLI_OPTS[@]}" cloudformation continue-update-rollback --stack-name "$stack_name" --region "$REGION"
        log_success "Rollback initiated"
    else
        log_info "Rollback cancelled"
    fi
}

# Post-deployment configuration
post_deployment_setup() {
    log_info "Running post-deployment setup..."
    
    local stack_name="$STACK_NAME-$ENVIRONMENT"
    
    # Get table names from stack outputs
    local config_table=$(aws "${AWS_CLI_OPTS[@]}" cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --region "$REGION" \
        --query 'Stacks[0].Outputs[?OutputKey==`ConfigTableName`].OutputValue' \
        --output text)
    
    if [[ -n "$config_table" && "$config_table" != "None" ]]; then
        log_info "Setting up default configuration in $config_table..."
        
        # Create default configuration
        cat > /tmp/default-config.json << EOF
{
    "config_key": {"S": "default"},
    "contact_types": {"SS": ["primary", "billing", "operations", "security"]},
    "excluded_accounts": {"SS": []},
    "retry_config": {
        "M": {
            "max_attempts": {"N": "3"},
            "base_delay": {"N": "2"},
            "max_delay": {"N": "60"}
        }
    },
    "notification_settings": {
        "M": {
            "notify_on_failure": {"BOOL": true},
            "notify_on_success": {"BOOL": false},
            "notify_on_partial_failure": {"BOOL": true},
            "failure_threshold": {"N": "1"}
        }
    }
}
EOF
        
        # Insert default configuration
        if aws "${AWS_CLI_OPTS[@]}" dynamodb put-item \
            --table-name "$config_table" \
            --item file:///tmp/default-config.json \
            --region "$REGION" &> /dev/null; then
            log_success "Default configuration created"
        else
            log_warning "Could not create default configuration (may already exist)"
        fi
        
        rm -f /tmp/default-config.json
    fi
    
    log_success "Post-deployment setup completed"
}

# Main execution
main() {
    log_info "Starting AWS Contact Sync deployment..."
    log_info "Environment: $ENVIRONMENT"
    log_info "Region: $REGION"
    log_info "Stack Name: $STACK_NAME-$ENVIRONMENT"
    if [[ -n "$AWS_PROFILE" ]]; then
        log_info "AWS Profile: $AWS_PROFILE"
    fi
    
    if [[ "$ROLLBACK" == true ]]; then
        rollback_deployment
        exit 0
    fi
    
    pre_deployment_checks
    
    if [[ "$VALIDATE_ONLY" == true ]]; then
        validate_template
        log_success "Template validation completed"
        exit 0
    fi
    
    build_application
    validate_template
    deploy_application
    get_stack_outputs
    post_deployment_setup
    
    echo
    log_success "Deployment completed successfully!"
    log_info "Stack Name: $STACK_NAME-$ENVIRONMENT"
    log_info "Region: $REGION"
    
    if [[ "$ENVIRONMENT" == "prod" ]]; then
        echo
        log_warning "Production deployment completed. Please verify the system is working correctly."
        log_info "Monitor the CloudWatch dashboard and check the logs for any issues."
    fi
}

# Run main function
main "$@"