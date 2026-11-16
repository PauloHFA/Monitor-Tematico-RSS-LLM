import httpx
import structlog

logger = structlog.get_logger(__name__)


class WhatsAppNotifier:
    """Optional WhatsApp Cloud/HTTP dispatcher isolated from Telegram delivery (RF18)."""

    def __init__(self, client: httpx.AsyncClient, *, api_url: str, api_token: str) -> None:
        self._client = client
        self._api_url = api_url
        self._api_token = api_token

    async def send(self, chat_id: int, text: str) -> bool:
        if not self._api_url.strip():
            return False
        headers = {"Authorization": f"Bearer {self._api_token}"} if self._api_token else {}
        try:
            response = await self._client.post(
                self._api_url,
                headers=headers,
                json={"to": str(chat_id), "text": text},
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("whatsapp_delivery_failed", chat_id=chat_id, error=str(exc))
            return False
