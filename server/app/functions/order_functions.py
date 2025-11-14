"""Function calling support for Voice Live API with mock order data."""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Mock order database
MOCK_ORDERS = {
    ("CUST001", "ORD12345"): {
        "order_id": "ORD12345",
        "customer_id": "CUST001", 
        "order_date": datetime(2025, 8, 15),
        "estimated_delivery": datetime(2025, 9, 5),
        "status": "In Transit",
        "items": ["Wireless Headphones", "USB Cable"]
    },
    ("CUST002", "ORD67890"): {
        "order_id": "ORD67890",
        "customer_id": "CUST002",
        "order_date": datetime(2025, 8, 20),
        "estimated_delivery": datetime(2025, 9, 1),
        "status": "Delivered",
        "items": ["Bluetooth Speaker", "Phone Case"]
    },
    ("CUST003", "ORD11111"): {
        "order_id": "ORD11111", 
        "customer_id": "CUST003",
        "order_date": datetime(2025, 8, 25),
        "estimated_delivery": datetime(2025, 9, 10),
        "status": "Processing",
        "items": ["Laptop Stand", "Wireless Mouse"]
    }
}

# Function definitions for Voice Live API
CHECK_ORDER_STATUS_DEF = {
    "name": "check_order_status",
    "description": "Check the status of a customer's order",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The unique identifier for the customer"
            },
            "order_id": {
                "type": "string", 
                "description": "The unique identifier for the order"
            }
        },
        "required": ["customer_id", "order_id"]
    }
}


def fetch_order_details(order_id: str, customer_id: str) -> tuple[datetime, str, datetime]:
    """Mock function to fetch order details from the database."""
    order_key = (customer_id, order_id)
    order = MOCK_ORDERS.get(order_key)
    
    if order:
        return order["estimated_delivery"], order["status"], order["order_date"]
    else:
        # Return default values for unknown orders
        return (
            datetime.now() + timedelta(days=7),
            "Order Not Found", 
            datetime.now() - timedelta(days=1)
        )


async def check_order_status_handler(customer_id: str, order_id: str) -> str:
    """Handle the check_order_status function call."""
    try:
        logger.info(f"Checking order status for customer {customer_id}, order {order_id}")
        
        # Fetch order details
        estimated_delivery, status, order_date = fetch_order_details(order_id, customer_id)
        
        # Determine status class for CSS
        status_class = status.lower().replace(" ", "-")
        
        # Read the HTML template
        template_path = os.path.join(os.path.dirname(__file__), '..', '..', 'order_status_template.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as file:
                html_content = file.read()
        except FileNotFoundError:
            logger.warning(f"Template file not found at {template_path}")
            # Fallback to simple text response
            return f"Order {order_id} status for customer {customer_id}: {status}. Order placed on {order_date.strftime('%B %d, %Y')}, estimated delivery: {estimated_delivery.strftime('%B %d, %Y')}"
        
        # Replace placeholders with actual data
        html_content = html_content.format(
            order_id=order_id,
            customer_id=customer_id,
            order_date=order_date.strftime("%B %d, %Y"),
            estimated_delivery=estimated_delivery.strftime("%B %d, %Y"),
            status=status,
            status_class=status_class
        )
        
        # For voice responses, we'll return a simple text summary instead of HTML
        voice_response = f"I found your order {order_id}. The status is {status}. It was placed on {order_date.strftime('%B %d, %Y')} and the estimated delivery date is {estimated_delivery.strftime('%B %d, %Y')}."
        
        logger.info(f"Order status check completed for {order_id}")
        return voice_response
        
    except Exception as e:
        logger.exception(f"Error handling order status check: {e}")
        return f"I'm sorry, I encountered an error while checking the status of order {order_id}. Please try again later."


# Function registry
FUNCTION_HANDLERS = {
    "check_order_status": check_order_status_handler
}

FUNCTION_DEFINITIONS = [
    CHECK_ORDER_STATUS_DEF
]


def get_function_definitions() -> list[Dict[str, Any]]:
    """Get all available function definitions."""
    return FUNCTION_DEFINITIONS


async def handle_function_call(function_name: str, arguments: Dict[str, Any]) -> str:
    """Handle a function call with the given name and arguments."""
    handler = FUNCTION_HANDLERS.get(function_name)
    if not handler:
        logger.error(f"Unknown function: {function_name}")
        return f"I'm sorry, I don't know how to handle the function '{function_name}'."
    
    try:
        # Call the handler with the unpacked arguments
        result = await handler(**arguments)
        return result
    except Exception as e:
        logger.exception(f"Error calling function {function_name}: {e}")
        return f"I'm sorry, I encountered an error while processing your request."
