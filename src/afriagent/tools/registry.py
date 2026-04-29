"""Tool registry — single source of truth for all tools.

Each tool entry describes what it does, what it requires,
what it returns, and how it behaves under failure.
"""

from __future__ import annotations

from typing import Any

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "check_invoice": {
        "description": "Check invoice status and details from WHMCS. Returns list of invoices with amounts, due dates, and payment status.",
        "requires": ["whmcs_client_id"],
        "returns": ["invoices", "total_due", "overdue_count"],
        "latency_profile": "medium",
        "cost": "free",
        "failure_mode": "returns_empty",
        "tool_class": "whmcs",
        "method": "get_invoices",
    },
    "mpesa_push": {
        "description": "Initiate M-Pesa STK Push payment request to customer's phone. Sends a payment prompt that the customer confirms with their PIN.",
        "requires": ["phone_number", "amount", "invoice_id"],
        "returns": ["success", "checkout_id", "message"],
        "latency_profile": "medium",
        "cost": "free",
        "failure_mode": "returns_error_with_message",
        "tool_class": "mpesa",
        "method": "request_payment",
    },
    "check_domain_dns": {
        "description": "Check DNS propagation and status for a domain. Verifies A, MX, NS, and CNAME records. Used for hosting troubleshooting.",
        "requires": ["domain"],
        "returns": ["dns_records", "propagation_status", "nameservers"],
        "latency_profile": "fast",
        "cost": "free",
        "failure_mode": "returns_error",
        "tool_class": "dns_check",
        "method": "check_domain",
    },
    "create_support_ticket": {
        "description": "Create a new support ticket in WHMCS. Assigns to appropriate department based on the issue type.",
        "requires": ["client_id", "subject", "message"],
        "returns": ["ticket_id", "ticket_number"],
        "latency_profile": "medium",
        "cost": "free",
        "failure_mode": "returns_error",
        "tool_class": "whmcs",
        "method": "create_ticket",
    },
    "lookup_customer": {
        "description": "Look up customer details from WHMCS including services, tickets, and invoices. Returns comprehensive customer context.",
        "requires": ["client_id"],
        "returns": ["client", "services", "open_tickets", "unpaid_invoices"],
        "latency_profile": "slow",
        "cost": "free",
        "failure_mode": "returns_empty",
        "tool_class": "whmcs",
        "method": "get_customer_context",
    },
    "check_invoice_status": {
        "description": "Check the status of a specific invoice by ID. Returns whether it's paid, unpaid, overdue, or cancelled.",
        "requires": ["invoice_id"],
        "returns": ["status", "amount", "due_date"],
        "latency_profile": "fast",
        "cost": "free",
        "failure_mode": "returns_empty",
        "tool_class": "whmcs",
        "method": "get_invoice",
    },
    "mpesa_query_status": {
        "description": "Query the status of an M-Pesa STK Push transaction using the CheckoutRequestID.",
        "requires": ["checkout_request_id"],
        "returns": ["result_code", "result_desc", "amount", "receipt_number"],
        "latency_profile": "fast",
        "cost": "free",
        "failure_mode": "returns_error",
        "tool_class": "mpesa",
        "method": "query_stk_status",
    },
}


def get_tool_info(tool_name: str) -> dict[str, Any] | None:
    """Get metadata for a specific tool."""
    return TOOL_REGISTRY.get(tool_name)


def get_all_tools() -> dict[str, dict[str, Any]]:
    """Get the full tool registry."""
    return dict(TOOL_REGISTRY)


def get_tools_by_latency(profile: str) -> list[str]:
    """Get tool names filtered by latency profile (fast/medium/slow)."""
    return [
        name for name, info in TOOL_REGISTRY.items()
        if info.get("latency_profile") == profile
    ]
