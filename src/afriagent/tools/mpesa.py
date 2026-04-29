"""M-Pesa Integration — STK Push, payment status, and callbacks."""

from __future__ import annotations

import base64
import time
from datetime import datetime
from typing import Any

import httpx

from afriagent.config import settings
from afriagent.config.logging import get_logger

log = get_logger(__name__)


class MpesaClient:
    """Safaricom M-Pesa Daraja API client."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30)
        self.consumer_key = settings.mpesa_consumer_key
        self.consumer_secret = settings.mpesa_consumer_secret
        self.shortcode = settings.mpesa_shortcode
        self.passkey = settings.mpesa_passkey
        self.callback_url = settings.mpesa_callback_url
        self._token: str = ""
        self._token_expires: float = 0

    # ── Auth ──────────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Get OAuth access token from Safaricom."""
        if self._token and time.time() < self._token_expires:
            return self._token

        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode()
        ).decode()

        resp = await self._http.get(
            "https://sandbox.safaricom.co.ke/oauth/v1/generate",
            params={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {credentials}"},
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 3599))
        return self._token

    # ── STK Push (Lipa Na M-Pesa Online) ─────────────────────────

    async def stk_push(
        self,
        phone_number: str,
        amount: float,
        account_reference: str,
        transaction_desc: str = "Payment",
    ) -> dict[str, Any]:
        """Initiate an STK Push request to customer's phone.

        Args:
            phone_number: Customer phone in format 254XXXXXXXXX
            amount: Amount in KSH
            account_reference: Reference for the transaction
            transaction_desc: Description shown to customer

        Returns:
            M-Pesa API response with CheckoutRequestID
        """
        token = await self._get_access_token()

        # Generate password
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()

        # Normalize phone number
        phone = phone_number.replace("+", "").replace("-", "").replace(" ", "")
        if phone.startswith("0"):
            phone = "254" + phone[1:]

        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": str(int(amount)),
            "PartyA": phone,
            "PartyB": self.shortcode,
            "PhoneNumber": phone,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference[:12],
            "TransactionDesc": transaction_desc[:100],
        }

        try:
            resp = await self._http.post(
                "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            result = resp.json()
            log.info(
                "STK Push initiated",
                phone=phone[:8] + "***",
                amount=amount,
                checkout_id=result.get("CheckoutRequestID"),
            )
            return result
        except Exception as e:
            log.error("STK Push failed", error=str(e), phone=phone[:8] + "***")
            return {"ResponseCode": "-1", "errorMessage": str(e)}

    # ── Transaction Status ────────────────────────────────────────

    async def query_stk_status(self, checkout_request_id: str) -> dict[str, Any]:
        """Query the status of an STK Push transaction."""
        token = await self._get_access_token()

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{self.shortcode}{self.passkey}{timestamp}".encode()
        ).decode()

        try:
            resp = await self._http.post(
                "https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query",
                json={
                    "BusinessShortCode": self.shortcode,
                    "Password": password,
                    "Timestamp": timestamp,
                    "CheckoutRequestID": checkout_request_id,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error("STK status query failed", error=str(e))
            return {"ResponseCode": "-1", "errorMessage": str(e)}

    # ── Helper for Support Agent ──────────────────────────────────

    async def request_payment(
        self,
        phone_number: str,
        amount: float,
        invoice_id: str,
    ) -> dict[str, Any]:
        """High-level payment request for the support agent.

        Returns a dict with status and user-friendly message.
        """
        result = await self.stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=f"INV-{invoice_id}",
            transaction_desc=f"Payment for Invoice #{invoice_id}",
        )

        response_code = result.get("ResponseCode", "-1")

        if response_code == "0":
            return {
                "success": True,
                "message": (
                    f"A payment request of KSH {amount:,.0f} has been sent to your phone. "
                    "Please check your phone and enter your M-Pesa PIN to complete the payment."
                ),
                "checkout_id": result.get("CheckoutRequestID"),
            }
        else:
            return {
                "success": False,
                "message": (
                    "I wasn't able to send the payment request to your phone. "
                    "Please try again or use the M-Pesa pay bill option directly."
                ),
                "error": result.get("errorMessage", result.get("ResponseDescription", "Unknown error")),
            }

    async def close(self) -> None:
        await self._http.aclose()
