"""
Alert Service - sends notifications via Telegram and Email.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from loguru import logger
from config.settings import get_settings

settings = get_settings()

# Lazy import telegram to avoid crash if not installed
try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class AlertService:
    """Sends trading alerts via Telegram and/or email."""

    def __init__(self):
        self._telegram_bot = None

    def _get_telegram_bot(self):
        if not TELEGRAM_AVAILABLE:
            return None
        if not settings.telegram_bot_token:
            return None
        if self._telegram_bot is None:
            self._telegram_bot = Bot(token=settings.telegram_bot_token)
        return self._telegram_bot

    async def send_telegram(self, message: str) -> bool:
        """Send a Telegram message."""
        bot = self._get_telegram_bot()
        if not bot or not settings.telegram_chat_id:
            return False
        try:
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=message,
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")
            return False

    async def send_email(self, subject: str, body: str) -> bool:
        """Send an email alert."""
        if not all([settings.smtp_user, settings.smtp_password, settings.alert_email]):
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.smtp_user
            msg["To"] = settings.alert_email
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)
            return True
        except Exception as e:
            logger.error(f"Email alert failed: {e}")
            return False

    async def notify_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy: str,
        simulated: bool = False,
    ):
        """Send a trade notification."""
        sim_tag = " [SIMULATED]" if simulated else ""
        emoji = "🟢" if side.upper() == "BUY" else "🔴"
        message = (
            f"{emoji} <b>Trade Alert{sim_tag}</b>\n"
            f"Strategy: {strategy}\n"
            f"Symbol: {symbol}\n"
            f"Side: {side.upper()}\n"
            f"Quantity: {quantity}\n"
            f"Price: ${price:,.4f}"
        )
        await self.send_telegram(message)
        await self.send_email(
            subject=f"Trade: {side.upper()} {symbol}{sim_tag}",
            body=message.replace("<b>", "").replace("</b>", ""),
        )

    async def notify_risk_event(self, event: str, details: str = ""):
        """Send a risk management alert."""
        message = (
            f"⚠️ <b>Risk Alert</b>\n"
            f"Event: {event}\n"
            f"Details: {details}"
        )
        await self.send_telegram(message)

    async def notify_error(self, source: str, error: str):
        """Send an error alert."""
        message = f"🚨 <b>Error</b>\nSource: {source}\n{error}"
        await self.send_telegram(message)


# Module singleton
alert_service = AlertService()
