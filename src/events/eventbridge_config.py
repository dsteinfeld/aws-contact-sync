"""EventBridge rule configuration for Account Management API events."""

import json
from typing import Dict, Any, List
from dataclasses import dataclass


@dataclass
class EventBridgeRuleConfig:
    """Configuration for EventBridge rules to capture Account Management events."""
    
    rule_name: str
    description: str
    event_pattern: Dict[str, Any]
    targets: List[Dict[str, Any]]
    
    def to_cloudformation(self) -> Dict[str, Any]:
        """Convert to CloudFormation EventBridge rule format."""
        return {
            "Type": "AWS::Events::Rule",
            "Properties": {
                "Name": self.rule_name,
                "Description": self.description,
                "EventPattern": self.event_pattern,
                "State": "ENABLED",
                "Targets": self.targets
            }
        }
    
    def to_sam_template(self) -> Dict[str, Any]:
        """Convert to SAM template EventBridge rule format."""
        return {
            "Type": "AWS::Events::Rule",
            "Properties": {
                "Name": self.rule_name,
                "Description": self.description,
                "EventPattern": self.event_pattern,
                "State": "ENABLED",
                "Targets": self.targets
            }
        }


class EventBridgeConfigGenerator:
    """Generator for EventBridge rule configurations."""
    
    def __init__(self, management_account_id: str):
        """Initialize with management account ID for filtering."""
        self.management_account_id = management_account_id
    
    def generate_contact_sync_rule(
        self, 
        lambda_function_arn: str,
        lambda_function_name: str = "ContactSyncHandler"
    ) -> EventBridgeRuleConfig:
        """
        Generate EventBridge rule for contact synchronization events.
        
        This rule captures Account Management API events (PutContactInformation, PutAlternateContact)
        from CloudTrail and routes them to the contact sync Lambda function.
        
        Critical filtering logic:
        - Only processes events from the management account (recipientAccountId)
        - Only processes management account operations (no accountId in requestParameters)
        - Filters for specific Account Management API calls
        
        Args:
            lambda_function_arn: ARN of the Lambda function to invoke
            lambda_function_name: Name of the Lambda function for target ID
            
        Returns:
            EventBridgeRuleConfig for contact synchronization
        """
        
        # Event pattern that matches CloudTrail events for Account Management API calls
        # Critical: This pattern ensures we only process management account contact changes
        event_pattern = {
            "source": ["aws.account"],
            "detail-type": ["AWS API Call via CloudTrail"],
            "detail": {
                "eventSource": ["account.amazonaws.com"],
                "eventName": [
                    "PutContactInformation",
                    "PutAlternateContact"
                ],
                # Critical filtering: Only process events from management account
                "recipientAccountId": [self.management_account_id],
                # Critical filtering: Only process management account operations
                # Management account operations do NOT have accountId in requestParameters
                "requestParameters": {
                    # This pattern ensures accountId is NOT present in requestParameters
                    # EventBridge doesn't support "not exists" directly, so we handle this in Lambda
                    # The Lambda function will filter out events that have accountId in requestParameters
                }
            }
        }
        
        # Lambda target configuration
        targets = [
            {
                "Id": f"{lambda_function_name}Target",
                "Arn": lambda_function_arn,
                "InputTransformer": {
                    # Transform the CloudTrail event to include only necessary fields
                    "InputPathsMap": {
                        "eventId": "$.detail.eventID",
                        "eventName": "$.detail.eventName",
                        "eventTime": "$.detail.eventTime",
                        "userIdentity": "$.detail.userIdentity",
                        "recipientAccountId": "$.detail.recipientAccountId",
                        "requestParameters": "$.detail.requestParameters",
                        "sourceIPAddress": "$.detail.sourceIPAddress",
                        "userAgent": "$.detail.userAgent"
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
                                    "recipientAccountId": "<recipientAccountId>",
                                    "requestParameters": "<requestParameters>",
                                    "sourceIPAddress": "<sourceIPAddress>",
                                    "userAgent": "<userAgent>"
                                }
                            }
                        ]
                    })
                }
            }
        ]
        
        return EventBridgeRuleConfig(
            rule_name="ContactSyncRule",
            description=f"Capture Account Management API contact changes in management account {self.management_account_id}",
            event_pattern=event_pattern,
            targets=targets
        )
    
    def generate_dlq_rule(
        self, 
        dlq_lambda_arn: str,
        dlq_lambda_name: str = "ContactSyncDLQHandler"
    ) -> EventBridgeRuleConfig:
        """
        Generate EventBridge rule for dead letter queue processing.
        
        This rule captures failed contact sync events for manual review and retry.
        
        Args:
            dlq_lambda_arn: ARN of the DLQ processing Lambda function
            dlq_lambda_name: Name of the DLQ Lambda function
            
        Returns:
            EventBridgeRuleConfig for DLQ processing
        """
        
        event_pattern = {
            "source": ["aws.lambda"],
            "detail-type": ["Lambda Function Invocation Result - Failure"],
            "detail": {
                "responseElements": {
                    "functionName": ["ContactSyncHandler"]
                }
            }
        }
        
        targets = [
            {
                "Id": f"{dlq_lambda_name}Target",
                "Arn": dlq_lambda_arn
            }
        ]
        
        return EventBridgeRuleConfig(
            rule_name="ContactSyncDLQRule",
            description="Process failed contact synchronization events",
            event_pattern=event_pattern,
            targets=targets
        )
    
    def generate_sam_template_section(
        self, 
        contact_sync_lambda_name: str,
        dlq_lambda_name: str = None
    ) -> Dict[str, Any]:
        """
        Generate complete SAM template section for EventBridge rules.
        
        Args:
            contact_sync_lambda_name: Name of the contact sync Lambda function
            dlq_lambda_name: Optional name of the DLQ Lambda function
            
        Returns:
            Dictionary containing SAM template resources for EventBridge rules
        """
        
        resources = {}
        
        # Main contact sync rule
        contact_sync_rule = self.generate_contact_sync_rule(
            lambda_function_arn=f"!GetAtt {contact_sync_lambda_name}.Arn",
            lambda_function_name=contact_sync_lambda_name
        )
        
        resources["ContactSyncEventRule"] = contact_sync_rule.to_sam_template()
        
        # Lambda permission for EventBridge to invoke the function
        resources["ContactSyncEventPermission"] = {
            "Type": "AWS::Lambda::Permission",
            "Properties": {
                "FunctionName": f"!Ref {contact_sync_lambda_name}",
                "Action": "lambda:InvokeFunction",
                "Principal": "events.amazonaws.com",
                "SourceArn": f"!GetAtt ContactSyncEventRule.Arn"
            }
        }
        
        # Optional DLQ rule
        if dlq_lambda_name:
            dlq_rule = self.generate_dlq_rule(
                dlq_lambda_arn=f"!GetAtt {dlq_lambda_name}.Arn",
                dlq_lambda_name=dlq_lambda_name
            )
            
            resources["ContactSyncDLQRule"] = dlq_rule.to_sam_template()
            
            resources["ContactSyncDLQPermission"] = {
                "Type": "AWS::Lambda::Permission",
                "Properties": {
                    "FunctionName": f"!Ref {dlq_lambda_name}",
                    "Action": "lambda:InvokeFunction",
                    "Principal": "events.amazonaws.com",
                    "SourceArn": f"!GetAtt ContactSyncDLQRule.Arn"
                }
            }
        
        return resources
    
    def get_event_pattern_documentation(self) -> str:
        """
        Get documentation explaining the EventBridge event pattern filtering logic.
        
        Returns:
            Detailed explanation of the filtering logic
        """
        return f"""
EventBridge Event Pattern Filtering Logic for Contact Synchronization

The EventBridge rule uses the following filtering strategy to ensure only management 
account contact changes trigger synchronization:

1. Source Filtering:
   - source: ["aws.account"] - Only Account Management service events
   - detail-type: ["AWS API Call via CloudTrail"] - Only CloudTrail API events

2. API Call Filtering:
   - eventSource: ["account.amazonaws.com"] - Account Management API only
   - eventName: ["PutContactInformation", "PutAlternateContact"] - Contact change events only

3. Account Filtering:
   - recipientAccountId: ["{self.management_account_id}"] - Only events from management account

4. Operation Type Filtering (Critical):
   - Management account operations: requestParameters does NOT contain "accountId"
   - Member account operations: requestParameters contains "accountId" (filtered out in Lambda)
   
   Note: EventBridge doesn't support "not exists" patterns, so the Lambda function
   performs the final filtering to exclude events with accountId in requestParameters.

5. Infinite Loop Prevention:
   - Events generated by the sync service itself are filtered out by checking userAgent
   - Member account updates (which would be triggered by this service) are ignored
   - Only original management account changes trigger new sync operations

This filtering ensures that:
- Only management account contact changes trigger synchronization
- Member account updates (caused by sync operations) don't create infinite loops
- The system processes both primary and alternate contact changes
- Failed events can be retried through DLQ processing
"""


def create_eventbridge_config(management_account_id: str) -> EventBridgeConfigGenerator:
    """
    Factory function to create EventBridge configuration generator.
    
    Args:
        management_account_id: AWS account ID of the organization management account
        
    Returns:
        EventBridgeConfigGenerator instance
    """
    return EventBridgeConfigGenerator(management_account_id)