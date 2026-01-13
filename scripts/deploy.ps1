# AWS Contact Synchronization System Deployment Script (PowerShell)
# This script automates the deployment of the contact sync system using AWS SAM

param(
    [string]$Environment = "prod",
    [string]$Region = "us-east-1", 
    [string]$AccountId = "",
    [string]$NotificationEmail = "",
    [string]$StackName = "aws-contact-sync",
    [switch]$Guided = $false,
    [switch]$NoConfirm = $false,
    [switch]$ValidateOnly = $false,
    [switch]$Rollback = $false,
    [switch]$Help = $false
)

# Color functions for output
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

# Usage function
function Show-Usage {
    Write-Host @"
Usage: .\scripts\deploy.ps1 [OPTIONS]

Deploy the AWS Contact Synchronization System

OPTIONS:
    -Environment ENV        Deployment environment (dev, staging, prod) [default: prod]
    -Region REGION          AWS region [default: us-east-1]
    -AccountId ACCOUNT      Management account ID (required)
    -NotificationEmail      Email for notifications (optional)
    -StackName NAME         CloudFormation stack name [default: aws-contact-sync]
    -Guided                 Use SAM guided deployment
    -NoConfirm              Skip deployment confirmation
    -ValidateOnly           Only validate template without deploying
    -Rollback               Rollback to previous version
    -Help                   Show this help message

EXAMPLES:
    # Deploy to production with management account ID
    .\scripts\deploy.ps1 -Environment prod -AccountId 123456789012 -NotificationEmail admin@company.com

    # Deploy to development environment
    .\scripts\deploy.ps1 -Environment dev -AccountId 123456789012 -Region us-west-2

    # Validate template only
    .\scripts\deploy.ps1 -ValidateOnly -AccountId 123456789012

    # Guided deployment
    .\scripts\deploy.ps1 -Guided

"@
}

# Validation functions
function Test-Environment {
    if ($Environment -notin @("dev", "staging", "prod")) {
        Write-Error "Invalid environment: $Environment. Must be dev, staging, or prod."
        exit 1
    }
}

function Test-AccountId {
    if ($AccountId -notmatch "^\d{12}$") {
        Write-Error "Invalid management account ID: $AccountId. Must be 12 digits."
        exit 1
    }
}

function Test-Email {
    if ($NotificationEmail -and $NotificationEmail -notmatch "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$") {
        Write-Error "Invalid email format: $NotificationEmail"
        exit 1
    }
}

function Test-AwsCli {
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        Write-Error "AWS CLI is not installed. Please install it first."
        exit 1
    }
    
    # Check AWS credentials
    try {
        aws sts get-caller-identity | Out-Null
    }
    catch {
        Write-Error "AWS credentials not configured. Please run 'aws configure' first."
        exit 1
    }
}

function Test-SamCli {
    if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
        Write-Error "SAM CLI is not installed. Please install it first."
        exit 1
    }
}

function Test-Permissions {
    Write-Info "Validating AWS permissions..."
    
    # This is a simplified check - in practice, you might want more comprehensive validation
    try {
        aws iam get-user | Out-Null
    }
    catch {
        $callerIdentity = aws sts get-caller-identity --query 'Arn' --output text
        if ($callerIdentity -notmatch 'role/') {
            Write-Warning "Unable to verify IAM permissions. Proceeding with deployment..."
        }
    }
}

# Pre-deployment checks
function Invoke-PreDeploymentChecks {
    Write-Info "Running pre-deployment checks..."
    
    Test-AwsCli
    Test-SamCli
    Test-Environment
    
    if (-not $Guided -and -not $ValidateOnly) {
        if (-not $AccountId) {
            Write-Error "Management account ID is required for non-guided deployment"
            exit 1
        }
        Test-AccountId
    }
    
    if ($NotificationEmail) {
        Test-Email
    }
    
    Test-Permissions
    
    # Check if we're in the right directory
    if (-not (Test-Path "template.yaml")) {
        Write-Error "template.yaml not found. Please run this script from the project root."
        exit 1
    }
    
    Write-Success "Pre-deployment checks passed"
}

# Build the SAM application
function Build-Application {
    Write-Info "Building SAM application..."
    
    # Clean previous builds
    if (Test-Path ".aws-sam") {
        Remove-Item -Recurse -Force ".aws-sam"
    }
    
    # Build the application
    $buildResult = sam build --cached --parallel
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SAM build failed"
        exit 1
    }
    
    Write-Success "Application built successfully"
}

# Validate the SAM template
function Test-Template {
    Write-Info "Validating SAM template..."
    
    $validateResult = sam validate --lint
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Template validation failed"
        exit 1
    }
    
    Write-Success "Template validation passed"
}

# Deploy the application
function Deploy-Application {
    Write-Info "Deploying application to $Environment environment..."
    
    # Prepare deployment parameters
    $deployParams = @(
        "--stack-name", "$StackName-$Environment",
        "--region", $Region,
        "--capabilities", "CAPABILITY_IAM CAPABILITY_NAMED_IAM",
        "--parameter-overrides",
        "Environment=$Environment",
        "ManagementAccountId=$AccountId"
    )
    
    # Add optional parameters
    if ($NotificationEmail) {
        $deployParams += "NotificationEmail=$NotificationEmail"
    }
    
    # Add confirmation flag
    if (-not $NoConfirm) {
        $deployParams += "--confirm-changeset"
    }
    
    # Deploy based on mode
    if ($Guided) {
        Write-Info "Starting guided deployment..."
        sam deploy --guided
    }
    else {
        Write-Info "Starting automated deployment..."
        $deployResult = sam deploy @deployParams --resolve-s3
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Deployment failed"
            exit 1
        }
    }
    
    Write-Success "Deployment completed successfully"
}

# Get stack outputs
function Get-StackOutputs {
    Write-Info "Retrieving stack outputs..."
    
    $fullStackName = "$StackName-$Environment"
    
    try {
        aws cloudformation describe-stacks --stack-name $fullStackName --region $Region | Out-Null
        
        Write-Host ""
        Write-Info "Stack Outputs:"
        aws cloudformation describe-stacks `
            --stack-name $fullStackName `
            --region $Region `
            --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue,Description]' `
            --output table
        
        # Get dashboard URL
        $dashboardUrl = aws cloudformation describe-stacks `
            --stack-name $fullStackName `
            --region $Region `
            --query 'Stacks[0].Outputs[?OutputKey==`DashboardURL`].OutputValue' `
            --output text
        
        if ($dashboardUrl -and $dashboardUrl -ne "None") {
            Write-Host ""
            Write-Info "CloudWatch Dashboard: $dashboardUrl"
        }
    }
    catch {
        Write-Warning "Could not retrieve stack outputs"
    }
}

# Rollback function
function Invoke-Rollback {
    Write-Info "Rolling back deployment..."
    
    $fullStackName = "$StackName-$Environment"
    
    try {
        aws cloudformation describe-stacks --stack-name $fullStackName --region $Region | Out-Null
    }
    catch {
        Write-Error "Stack $fullStackName does not exist"
        exit 1
    }
    
    Write-Warning "This will rollback the stack to the previous version. Are you sure? (y/N)"
    $response = Read-Host
    if ($response -eq "y" -or $response -eq "Y") {
        aws cloudformation cancel-update-stack --stack-name $fullStackName --region $Region
        aws cloudformation continue-update-rollback --stack-name $fullStackName --region $Region
        Write-Success "Rollback initiated"
    }
    else {
        Write-Info "Rollback cancelled"
    }
}

# Post-deployment configuration
function Set-PostDeploymentConfig {
    Write-Info "Running post-deployment setup..."
    
    $fullStackName = "$StackName-$Environment"
    
    # Get table names from stack outputs
    $configTable = aws cloudformation describe-stacks `
        --stack-name $fullStackName `
        --region $Region `
        --query 'Stacks[0].Outputs[?OutputKey==`ConfigTableName`].OutputValue' `
        --output text
    
    if ($configTable -and $configTable -ne "None") {
        Write-Info "Setting up default configuration in $configTable..."
        
        # Create default configuration
        $defaultConfig = @{
            config_key = @{ S = "default" }
            contact_types = @{ SS = @("primary", "billing", "operations", "security") }
            excluded_accounts = @{ SS = @() }
            retry_config = @{
                M = @{
                    max_attempts = @{ N = "3" }
                    base_delay = @{ N = "2" }
                    max_delay = @{ N = "60" }
                }
            }
            notification_settings = @{
                M = @{
                    notify_on_failure = @{ BOOL = $true }
                    notify_on_success = @{ BOOL = $false }
                    notify_on_partial_failure = @{ BOOL = $true }
                    failure_threshold = @{ N = "1" }
                }
            }
        }
        
        $configJson = $defaultConfig | ConvertTo-Json -Depth 10
        $tempFile = [System.IO.Path]::GetTempFileName()
        $configJson | Out-File -FilePath $tempFile -Encoding UTF8
        
        # Insert default configuration
        try {
            aws dynamodb put-item `
                --table-name $configTable `
                --item "file://$tempFile" `
                --region $Region | Out-Null
            Write-Success "Default configuration created"
        }
        catch {
            Write-Warning "Could not create default configuration (may already exist)"
        }
        finally {
            Remove-Item $tempFile -ErrorAction SilentlyContinue
        }
    }
    
    Write-Success "Post-deployment setup completed"
}

# Main execution
function Main {
    if ($Help) {
        Show-Usage
        return
    }
    
    Write-Info "Starting AWS Contact Sync deployment..."
    Write-Info "Environment: $Environment"
    Write-Info "Region: $Region"
    Write-Info "Stack Name: $StackName-$Environment"
    
    if ($Rollback) {
        Invoke-Rollback
        return
    }
    
    Invoke-PreDeploymentChecks
    
    if ($ValidateOnly) {
        Test-Template
        Write-Success "Template validation completed"
        return
    }
    
    Build-Application
    Test-Template
    Deploy-Application
    Get-StackOutputs
    Set-PostDeploymentConfig
    
    Write-Host ""
    Write-Success "Deployment completed successfully!"
    Write-Info "Stack Name: $StackName-$Environment"
    Write-Info "Region: $Region"
    
    if ($Environment -eq "prod") {
        Write-Host ""
        Write-Warning "Production deployment completed. Please verify the system is working correctly."
        Write-Info "Monitor the CloudWatch dashboard and check the logs for any issues."
    }
}

# Run main function
Main