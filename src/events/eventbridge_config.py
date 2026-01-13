"""EventBridge rule configuration for Account Management API events."""

import json
from typing import Dict, Any, List
from dataclasses import dataclass


@dataclass
class EventBridgeRuleConfig:
    """Configuration for EventBridge rules that trigger contact synchronization."""
    
    rule_name: str
    description: str
    event_pattern: Dict[str, Any]
    targets: List[Dict[str, Any]]
    state: str = "ENABLED"

    def to_cloudformation(self) -> Dict[str, Any]:
        """Convert to CloudFormation EventBridge rule resource."""
        return {
            "Type": "AWS::Events::Rule",
            "Properties": {
                "Name": self.rule_name,
                "Description": self.description,
                "EventPattern": self.event_pattern,
                "State": self.state,
                "Targets": self.targets
            }
        }

    def to_sam_template(self) -> Dict[str, Any]:
        """Convert to SAM template EventBridge rule resource."""
        return {
            "Type": "AWS::Events::Rule",
            "Properties": {
                "Name": self.rule_name,
                "Description": self.description,
                "EventPattern": self.event_pattern,
                "State": self.state,
                "Targets": self.targets
            }
        }


class ContactSyncEventBridgeConfig:
    """EventBridge configuration for contact synchronization system."""
    
    def __init__(self, management_account_id: str, lambda_function_arn: str):
        """
        Initialize EventBridge configuration.
        
        Args:
            management_account_id: AWS account ID of the management account
            lambda_function_arn: ARN of the Lambda function to trigger
        """
        self.management_account_id = management_account_id
        self.lambda_function_arn = lambda_function_arn

    def get_contact_sync_rule(self) -> EventBridgeRuleConfig:
        """
        Get EventBridge rule configuration for contact synchronization.
        
        This rule captures Account Management API events from CloudTrail
        and filters for management account contact changes only.
        
        Returns:
            EventBridgeRuleConfig for contact synchronization
        """
        # Event pattern that matches CloudTrail events for Account Management API
        # CRITICAL: Only process management account changes by filtering out
        # events that have accountId in requestParameters
        event_pattern = {
            "source": ["aws.account"],
            "detail-type": ["AWS API Call via CloudTrail"],
            "detail": {
                "eventSource": ["account.amazonaws.com"],
                "eventName": [
                    "PutContactInformation",
                    "PutAlternateContact"
                ],
                # CRITICAL FILTER: Only process management account operations
                # Management account operations do NOT have accountId in requestParameters
                # This prevents infinite loops from member account updates
                "recipientAccountId": [self.management_account_id],
                "requestParameters": {
                    # This filter ensures accountId is NOT present in requestParameters
                    # When accountId is present, it indicates a member account operation
                    # When accountId is absent, it indicates a management account operation
                    "accountId": {
                        "exists": False
                    }
                }
            }
        }
        
        # Lambda target configuration
        targets = [
            {
                "Id": "ContactSyncLambdaTarget",
                "Arn": self.lambda_function_arn,
                "InputTransformer": {
                    "InputPathsMap": {
                        "eventId": "$.detail.eventID",
                        "eventName": "$.detail.eventName",
                        "eventTime": "$.detail.eventTime",
                        "userIdentity": "$.detail.userIdentity",
                        "requestParameters": "$.detail.requestParameters",
                        "recipientAccountId": "$.detail.recipientAccountId"
                    },
                    "InputTemplate": json.dumps({
                        "Records": [
                            {
                                "eventSource": "aws:events",
                                "eventName": "ContactChangeEvent",
                                "detail": {
                                    "eventID": "<eventId>",
                                    "eventName": "<eventName>",
                                    "eventTime": "<eventTime>",
                                    "userIdentity": "<userIdentity>",
                                    "requestParameters": "<requestParameters>",
                                    "recipientAccountId": "<recipientAccountId>"
                                }
                            }
                        ]
                    })
                }
            }
        ]
        
        return EventBridgeRuleConfig(
            rule_name="ContactSyncRule",
            description="Triggers contact synchronization when management account contacts change",
            event_pattern=event_pattern,
            targets=targets
        )

    def get_sam_template_resources(self) -> Dict[str, Any]:
        """
        Get complete SAM template resources for EventBridge integration.
        
        Returns:
            Dictionary of SAM template resources
        """
        contact_sync_rule = self.get_contact_sync_rule()
        
        resources = {
            "ContactSyncEventRule": contact_sync_rule.to_sam_template(),
            
            # Lambda permission to allow EventBridge to invoke the function
            "ContactSyncLambdaPermission": {
                "Type": "AWS::Lambda::Permission",
                "Properties": {
                    "FunctionName": self.lambda_function_arn,
                    "Action": "lambda:InvokeFunction",
                    "Principal": "events.amazonaws.com",
                    "SourceArn": {
                        "Fn::GetAtt": ["ContactSyncEventRule", "Arn"]
                    }
                }
            }
        }
        
        return resources

    def get_cloudformation_template(self) -> Dict[str, Any]:
        """
        Get complete CloudFormation template for EventBridge integration.
        
        Returns:
            Complete CloudFormation template
        """
        resources = self.get_sam_template_resources()
        
        template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": "EventBridge rules for AWS Contact Synchronization",
            "Parameters": {
                "ManagementAccountId": {
                    "Type": "String",
                    "Description": "AWS Account ID of the management account",
                    "Default": self.management_account_id
                },
                "LambdaFunctionArn": {
                    "Type": "String",
                    "Description": "ARN of the Lambda function to trigger",
                    "Default": self.lambda_function_arn
                }
            },
            "Resources": resources
        }
        
        return template

    def validate_event_pattern(self, cloudtrail_event: Dict[str, Any]) -> bool:
        """
        Validate if a CloudTrail event matches the EventBridge rule pattern.
        
        This is useful for testing and debugging the event filtering logic.
        
        Args:
            cloudtrail_event: CloudTrail event to validate
            
        Returns:
            True if event matches the pattern, False otherwise
        """
        try:
            # Check event source
            if cloudtrail_event.get("source") != "aws.account":
                return False
            
            # Check detail type
            if cloudtrail_event.get("detail-type") != "AWS API Call via CloudTrail":
                return False
            
            detail = cloudtrail_event.get("detail", {})
            
            # Check event source
            if detail.get("eventSource") != "account.amazonaws.com":
                return False
            
            # Check event name
            if detail.get("eventName") not in ["PutContactInformation", "PutAlternateContact"]:
                return False
            
            # Check recipient account ID
            if detail.get("recipientAccountId") != self.management_account_id:
                return False
            
            # CRITICAL: Check that accountId is NOT in requestParameters
            # This is the key filter that prevents infinite loops
            request_params = detail.get("requestParameters", {})
            if "accountId" in request_params:
                return False  # This is a member account operation, should be filtered out
            
            return True
            
        except Exception:
            return False

    def get_event_pattern_explanation(self) -> str:
        """
        Get human-readable explanation of the event pattern filtering logic.
        
        Returns:
            Detailed explanation of the filtering logic
        """
        return """
EventBridge Rule Filtering Logic for Contact Synchronization:

1. Source Filter: Only events from 'aws.account' service
2. Detail Type: Only 'AWS API Call via CloudTrail' events
3. Event Source: Only 'account.amazonaws.com' (Account Management API)
4. Event Names: Only 'PutContactInformation' and 'PutAlternateContact'
5. Recipient Account: Only events from the management account
6. CRITICAL FILTER: Only events WITHOUT 'accountId' in requestParameters

The critical filter (#6) prevents infinite loops:
- Management account operations: NO 'accountId' in requestParameters → PROCESS
- Member account operations: HAS 'accountId' in requestParameters → IGNORE

This ensures that when the system updates member accounts, those operations
don't trigger additional synchronization events, preventing infinite loops.

Note: 'recipientAccountId' is always the management account ID regardless of
which account is being updated, so it cannot be used for filtering.
"""