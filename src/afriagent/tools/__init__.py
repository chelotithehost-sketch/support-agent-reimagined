"""Tool registry — central access to external integrations."""

from __future__ import annotations

from afriagent.config.logging import get_logger
from afriagent.tools.whmcs import WHMCSClient
from afriagent.tools.mpesa import MpesaClient

log = get_logger(__name__)


class ToolRegistry:
    """Manages external tool instances."""

    def __init__(self) -> None:
        self.whmcs = WHMCSClient()
        self.mpesa = MpesaClient()

    async def close_all(self) -> None:
        await self.whmcs.close()
        await self.mpesa.close()
        log.info("All tools disconnected")
