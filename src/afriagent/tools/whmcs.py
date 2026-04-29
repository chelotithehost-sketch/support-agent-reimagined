"""WHMCS Integration — Client management, billing, and ticketing."""

from __future__ import annotations

from typing import Any

import httpx

from afriagent.config import settings
from afriagent.config.logging import get_logger

log = get_logger(__name__)


class WHMCSClient:
    """Async WHMCS API client for client management and billing."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        self.base_url = settings.whmcs_url.rstrip("/")
        self.identifier = settings.whmcs_identifier
        self.secret = settings.whmcs_secret

    async def _api_call(self, action: str, **params: Any) -> dict[str, Any]:
        """Make an authenticated WHMCS API call."""
        data = {
            "identifier": self.identifier,
            "secret": self.secret,
            "action": action,
            "responsetype": "json",
            **params,
        }
        try:
            resp = await self._http.post(f"{self.base_url}/includes/api.php", data=data)
            resp.raise_for_status()
            result = resp.json()
            if result.get("result") == "error":
                log.warning("WHMCS API error", action=action, message=result.get("message"))
            return result
        except Exception as e:
            log.error("WHMCS API call failed", action=action, error=str(e))
            return {"result": "error", "message": str(e)}

    # ── Client Operations ────────────────────────────────────────

    async def get_client(self, client_id: int) -> dict[str, Any]:
        """Get client details by ID."""
        return await self._api_call("GetClientsDetails", clientid=client_id)

    async def get_client_by_email(self, email: str) -> dict[str, Any]:
        """Find client by email address."""
        return await self._api_call("GetClients", search=email)

    async def update_client(self, client_id: int, **fields: Any) -> dict[str, Any]:
        """Update client information."""
        return await self._api_call("UpdateClient", clientid=client_id, **fields)

    # ── Service Operations ───────────────────────────────────────

    async def get_client_services(self, client_id: int) -> dict[str, Any]:
        """Get all active services for a client."""
        return await self._api_call("GetClientsProducts", clientid=client_id)

    async def get_service_details(self, service_id: int) -> dict[str, Any]:
        """Get details of a specific service."""
        return await self._api_call("GetClientsProducts", serviceid=service_id)

    # ── Ticket Operations ────────────────────────────────────────

    async def get_tickets(self, client_id: int) -> dict[str, Any]:
        """Get all tickets for a client."""
        return await self._api_call("GetTickets", clientid=client_id)

    async def create_ticket(
        self,
        client_id: int,
        subject: str,
        message: str,
        priority: str = "Medium",
        department_id: int = 1,
    ) -> dict[str, Any]:
        """Create a new support ticket."""
        return await self._api_call(
            "OpenTicket",
            clientid=client_id,
            subject=subject,
            message=message,
            priority=priority,
            deptid=department_id,
        )

    async def add_ticket_reply(
        self, ticket_id: int, message: str
    ) -> dict[str, Any]:
        """Add a reply to an existing ticket."""
        return await self._api_call(
            "AddTicketReply",
            ticketid=ticket_id,
            message=message,
        )

    async def close_ticket(self, ticket_id: int) -> dict[str, Any]:
        """Close a support ticket."""
        return await self._api_call("CloseTicket", ticketid=ticket_id)

    # ── Invoice Operations ───────────────────────────────────────

    async def get_invoices(self, client_id: int) -> dict[str, Any]:
        """Get all invoices for a client."""
        return await self._api_call("GetInvoices", userid=client_id)

    async def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Get invoice details."""
        return await self._api_call("GetInvoice", invoiceid=invoice_id)

    async def create_invoice(
        self,
        client_id: int,
        items: list[dict[str, Any]],
        due_date: str = "",
    ) -> dict[str, Any]:
        """Create a new invoice."""
        params: dict[str, Any] = {
            "userid": client_id,
        }
        for i, item in enumerate(items):
            params[f"description{i}"] = item["description"]
            params[f"amount{i}"] = item["amount"]
            params[f"qty{i}"] = item.get("quantity", 1)
        if due_date:
            params["duedate"] = due_date
        return await self._api_call("CreateInvoice", **params)

    # ── Helper Methods ───────────────────────────────────────────

    async def get_customer_context(self, client_id: int) -> dict[str, Any]:
        """Get comprehensive customer context for the Brain."""
        client = await self.get_client(client_id)
        services = await self.get_client_services(client_id)
        tickets = await self.get_tickets(client_id)
        invoices = await self.get_invoices(client_id)

        return {
            "client": client,
            "services": services.get("products", {}).get("product", []),
            "open_tickets": [
                t for t in tickets.get("tickets", {}).get("ticket", [])
                if t.get("status") != "Closed"
            ],
            "unpaid_invoices": [
                inv for inv in invoices.get("invoices", {}).get("invoice", [])
                if inv.get("status") == "Unpaid"
            ],
        }

    async def close(self) -> None:
        await self._http.aclose()
