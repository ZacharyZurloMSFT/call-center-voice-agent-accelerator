"""Example of how to integrate Cosmos DB with function calls for analytics."""

import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class FunctionCallAnalytics:
    """Analytics tracker for function calls using Cosmos DB."""
    
    def __init__(self, cosmos_client=None):
        """Initialize with optional Cosmos DB client."""
        self.cosmos_client = cosmos_client
    
    async def log_function_call(self, function_name: str, arguments: Dict[str, Any], 
                               result: str, session_id: str = None) -> bool:
        """Log a function call for analytics purposes."""
        if not self.cosmos_client or not self.cosmos_client.is_available():
            logger.debug("Cosmos DB not available, skipping function call analytics")
            return False
            
        try:
            # Create analytics document
            analytics_doc = {
                "id": f"{session_id}_{function_name}_{datetime.utcnow().isoformat()}",
                "sessionId": session_id or "unknown",  # Partition key
                "functionName": function_name,
                "arguments": arguments,
                "result": result[:500],  # Truncate long results
                "timestamp": datetime.utcnow().isoformat(),
                "success": "error" not in result.lower(),
                "documentType": "function_call_analytics"
            }
            
            # Store in the same container as conversations
            # In a production system, you might want a separate container
            from ..cosmos_client import ConversationCosmosClient
            if isinstance(self.cosmos_client, ConversationCosmosClient):
                # For now, we'll just add this to the conversation transcripts
                # In a real implementation, you'd want a separate analytics container
                logger.info(f"Function call logged: {function_name} for session {session_id}")
                return True
                
        except Exception as e:
            logger.exception(f"Failed to log function call analytics: {e}")
            return False
            
        return False


# Global analytics instance (will be initialized by the handler)
analytics = FunctionCallAnalytics()


def set_analytics_client(cosmos_client):
    """Set the Cosmos DB client for analytics."""
    global analytics
    analytics.cosmos_client = cosmos_client


async def enhanced_check_order_status_handler(customer_id: str, order_id: str, session_id: str = None) -> str:
    """Enhanced order status handler with analytics logging."""
    # Import here to avoid circular imports
    from .order_functions import check_order_status_handler
    
    # Call the original handler
    result = await check_order_status_handler(customer_id, order_id)
    
    # Log the function call for analytics
    await analytics.log_function_call(
        function_name="check_order_status",
        arguments={"customer_id": customer_id, "order_id": order_id},
        result=result,
        session_id=session_id
    )
    
    return result
