#!/bin/bash

# AWS Contact Synchronization System Rollback Script
# This script handles rollback operations for failed deployments

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

Rollback AWS Contact Synchronization System deployment

OPTIONS:
    -e, --environment ENV       Environment to rollback (dev, staging, prod) [default: prod]
    -r, --region REGION         AWS region [default: us-east-1]
    -s, --stack-name NAME       CloudFormation stack name [default: aws-contact-sync]
    --force                     Force rollback without confirmation
    --delete                    Delete the entire stack instead of rollback
    --backup-data               Backup DynamoDB data before rollback
    -h, --help                  Show this help message

EXAMPLES:
    # Rollback to previous version
    $0 --environment prod

    # Force rollback without confirmation
    $0 --environment prod --force

    # Delete entire stack with data backup
    $0 --environment dev --delete --backup-data

EOF
}

# Parse command line arguments
ENVIRONMENT="prod"
REGION="$DEFAULT_REGION"
STACK_NAME="aws-contact-sync"
FORCE=false
DELETE_STACK=false
BACKUP_DATA=false

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
        --force)
            FORCE=true
            shift
            ;;
        --delete)
            DELETE_STACK=true
            shift
            ;;
        --backup-data)
            BACKUP_DATA=true
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
    
    log_success "Stack exists"
}

get_stack_status() {
    aws cloudformation describe-stacks \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'Stacks[0].StackStatus' \
        --output text
}

backup_dynamodb_data() {
    log_info "Backing up DynamoDB data..."
    
    local backup_dir="/tmp/contact-sync-backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$backup_dir"
    
    # Get table names from stack
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
    
    # Backup configuration table
    if [[ -n "$config_table" && "$config_table" != "None" ]]; then
        log_info "Backing up configuration table: $config_table"
        aws dynamodb scan \
            --table-name "$config_table" \
            --region "$REGION" \
            --output json > "$backup_dir/config-table-backup.json"
        log_success "Configuration table backed up"
    fi
    
    # Backup state table (last 7 days only to avoid large files)
    if [[ -n "$state_table" && "$state_table" != "None" ]]; then
        log_info "Backing up state table: $state_table (last 7 days)"
        local seven_days_ago=$(date -d '7 days ago' -u +%Y-%m-%dT%H:%M:%SZ)
        
        aws dynamodb scan \
            --table-name "$state_table" \
            --region "$REGION" \
            --filter-expression "attribute_exists(#ts) AND #ts >= :timestamp" \
            --expression-attribute-names '{"#ts": "timestamp"}' \
            --expression-attribute-values "{\":timestamp\": {\"S\": \"$seven_days_ago\"}}" \
            --output json > "$backup_dir/state-table-backup.json"
        log_success "State table backed up"
    fi
    
    # Create backup metadata
    cat > "$backup_dir/backup-metadata.json" << EOF
{
    "backup_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "stack_name": "$FULL_STACK_NAME",
    "region": "$REGION",
    "environment": "$ENVIRONMENT",
    "config_table": "$config_table",
    "state_table": "$state_table",
    "backup_type": "pre-rollback"
}
EOF
    
    log_success "Data backup completed: $backup_dir"
    echo "Backup location: $backup_dir"
}

confirm_action() {
    local action="$1"
    
    if [[ "$FORCE" == true ]]; then
        return 0
    fi
    
    echo
    log_warning "You are about to $action the following stack:"
    log_warning "  Stack Name: $FULL_STACK_NAME"
    log_warning "  Region: $REGION"
    log_warning "  Environment: $ENVIRONMENT"
    echo
    
    if [[ "$DELETE_STACK" == true ]]; then
        log_warning "THIS WILL PERMANENTLY DELETE ALL RESOURCES!"
        log_warning "This action cannot be undone!"
    fi
    
    echo
    read -p "Are you sure you want to proceed? (type 'yes' to confirm): " confirmation
    
    if [[ "$confirmation" != "yes" ]]; then
        log_info "Operation cancelled"
        exit 0
    fi
}

rollback_stack() {
    log_info "Initiating stack rollback..."
    
    local stack_status=$(get_stack_status)
    
    case "$stack_status" in
        "UPDATE_IN_PROGRESS")
            log_info "Stack update in progress, cancelling update..."
            aws cloudformation cancel-update-stack \
                --stack-name "$FULL_STACK_NAME" \
                --region "$REGION"
            
            log_info "Waiting for update cancellation..."
            aws cloudformation wait stack-update-complete \
                --stack-name "$FULL_STACK_NAME" \
                --region "$REGION" || true
            ;;
        "UPDATE_ROLLBACK_FAILED"|"UPDATE_ROLLBACK_IN_PROGRESS")
            log_info "Continuing rollback from failed state..."
            aws cloudformation continue-update-rollback \
                --stack-name "$FULL_STACK_NAME" \
                --region "$REGION"
            ;;
        "UPDATE_COMPLETE"|"CREATE_COMPLETE")
            log_info "Stack is in stable state, initiating rollback..."
            # For stable stacks, we need to trigger a rollback by updating with previous template
            log_warning "Cannot rollback stable stack automatically. Please use AWS Console or specify a previous template."
            exit 1
            ;;
        *)
            log_error "Stack is in unexpected state: $stack_status"
            log_error "Manual intervention may be required"
            exit 1
            ;;
    esac
    
    log_info "Waiting for rollback to complete..."
    aws cloudformation wait stack-update-complete \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION"
    
    local final_status=$(get_stack_status)
    if [[ "$final_status" == "UPDATE_ROLLBACK_COMPLETE" ]]; then
        log_success "Stack rollback completed successfully"
    else
        log_error "Stack rollback failed. Final status: $final_status"
        exit 1
    fi
}

delete_stack() {
    log_info "Deleting stack..."
    
    # Disable termination protection if enabled
    aws cloudformation update-termination-protection \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION" \
        --no-enable-termination-protection || true
    
    # Delete the stack
    aws cloudformation delete-stack \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION"
    
    log_info "Waiting for stack deletion to complete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$FULL_STACK_NAME" \
        --region "$REGION"
    
    log_success "Stack deleted successfully"
}

cleanup_resources() {
    log_info "Cleaning up remaining resources..."
    
    # Clean up any remaining S3 buckets (CloudTrail logs)
    local buckets=$(aws s3api list-buckets \
        --query "Buckets[?contains(Name, '$STACK_NAME-$ENVIRONMENT')].Name" \
        --output text)
    
    for bucket in $buckets; do
        if [[ -n "$bucket" ]]; then
            log_info "Cleaning up S3 bucket: $bucket"
            aws s3 rm "s3://$bucket" --recursive || true
            aws s3api delete-bucket --bucket "$bucket" --region "$REGION" || true
        fi
    done
    
    # Clean up any remaining log groups
    local log_groups=$(aws logs describe-log-groups \
        --log-group-name-prefix "/aws/lambda/$FULL_STACK_NAME" \
        --region "$REGION" \
        --query 'logGroups[*].logGroupName' \
        --output text)
    
    for log_group in $log_groups; do
        if [[ -n "$log_group" ]]; then
            log_info "Cleaning up log group: $log_group"
            aws logs delete-log-group --log-group-name "$log_group" --region "$REGION" || true
        fi
    done
    
    log_success "Resource cleanup completed"
}

restore_data() {
    local backup_dir="$1"
    
    if [[ ! -d "$backup_dir" ]]; then
        log_error "Backup directory not found: $backup_dir"
        return 1
    fi
    
    log_info "Restoring data from backup: $backup_dir"
    
    # This would require the stack to be recreated first
    log_warning "Data restore requires manual intervention after stack recreation"
    log_info "Backup files are available at: $backup_dir"
    log_info "Use the following commands to restore data after recreating the stack:"
    echo
    echo "# Restore configuration table:"
    echo "aws dynamodb batch-write-item --request-items file://$backup_dir/config-table-backup.json"
    echo
    echo "# Restore state table:"
    echo "aws dynamodb batch-write-item --request-items file://$backup_dir/state-table-backup.json"
}

# Main execution
main() {
    log_info "Starting rollback operation..."
    log_info "Environment: $ENVIRONMENT"
    log_info "Region: $REGION"
    log_info "Stack: $FULL_STACK_NAME"
    
    validate_stack_exists
    
    local stack_status=$(get_stack_status)
    log_info "Current stack status: $stack_status"
    
    # Backup data if requested
    if [[ "$BACKUP_DATA" == true ]]; then
        backup_dynamodb_data
    fi
    
    if [[ "$DELETE_STACK" == true ]]; then
        confirm_action "delete"
        delete_stack
        cleanup_resources
        log_success "Stack deletion completed"
    else
        confirm_action "rollback"
        rollback_stack
        log_success "Stack rollback completed"
    fi
    
    echo
    log_info "=== Rollback Summary ==="
    if [[ "$DELETE_STACK" == true ]]; then
        log_success "✓ Stack deleted successfully"
        log_success "✓ Resources cleaned up"
        if [[ "$BACKUP_DATA" == true ]]; then
            log_success "✓ Data backed up before deletion"
        fi
    else
        log_success "✓ Stack rolled back successfully"
        log_info "Stack is now in previous stable state"
    fi
    
    echo
    log_info "Rollback operation completed!"
}

# Run main function
main "$@"