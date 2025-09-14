"""SMTP email sending functionality."""

import smtplib
import ssl
import logging
from typing import List, Dict, Optional, Union
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from src.models.smtp_config import SMTPConfig
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailSender:
    """SMTP client for sending emails."""

    def __init__(self, smtp_config: SMTPConfig):
        self.config = smtp_config
        self._server = None

    async def connect(self) -> bool:
        """Connect to SMTP server."""
        try:
            # Create SMTP connection using smtp_port if available, otherwise fall back to port
            smtp_port = getattr(self.config, 'smtp_port', self.config.port)

            # Use specific SMTP SSL/TLS settings
            smtp_use_ssl = getattr(self.config, 'smtp_use_ssl', False)
            smtp_use_tls = getattr(self.config, 'smtp_use_tls', True)

            if smtp_use_ssl:
                # Use SSL connection (typically port 465)
                self._server = smtplib.SMTP_SSL(self.config.host, smtp_port, timeout=10)
            else:
                # Use regular connection with optional TLS (typically port 587)
                self._server = smtplib.SMTP(self.config.host, smtp_port, timeout=10)
                if smtp_use_tls:
                    context = ssl.create_default_context()
                    self._server.starttls(context=context)

            # Login
            self._server.login(self.config.username, self.config.password)
            logger.info(f"Connected to SMTP server {self.config.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to SMTP server {self.config.name}: {e}")
            return False

    def disconnect(self):
        """Disconnect from SMTP server."""
        if self._server:
            try:
                self._server.quit()
                logger.info(f"Disconnected from SMTP server {self.config.name}")
            except Exception as e:
                logger.error(f"Error disconnecting from SMTP server {self.config.name}: {e}")
            finally:
                self._server = None

    async def send_email(self,
                        to_addresses: List[str],
                        subject: str,
                        body_text: Optional[str] = None,
                        body_html: Optional[str] = None,
                        cc_addresses: Optional[List[str]] = None,
                        bcc_addresses: Optional[List[str]] = None,
                        attachments: Optional[List[Dict]] = None,
                        reply_to: Optional[str] = None,
                        in_reply_to: Optional[str] = None,
                        references: Optional[str] = None) -> Dict[str, Union[bool, str]]:
        """
        Send an email with optional attachments.

        Args:
            to_addresses: List of recipient email addresses
            subject: Email subject
            body_text: Plain text body (optional)
            body_html: HTML body (optional)
            cc_addresses: CC recipients (optional)
            bcc_addresses: BCC recipients (optional)
            attachments: List of attachment dicts with 'data' and 'filename' keys
            reply_to: Reply-to address (optional)
            in_reply_to: Message ID this is a reply to (for threading)
            references: References header for email threading

        Returns:
            Dict with 'success' bool and 'message' string
        """
        if not self._server:
            if not await self.connect():
                return {"success": False, "message": "Failed to connect to SMTP server"}

        try:
            # Create message
            msg = MIMEMultipart('mixed')

            # Set headers - use account_name if available, otherwise username
            from_email = getattr(self.config, 'account_name', self.config.username)
            if not from_email or '@' not in from_email:
                from_email = self.config.username  # fallback
            msg['From'] = from_email
            msg['To'] = ', '.join(to_addresses)
            msg['Subject'] = subject
            msg['Date'] = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S +0000')

            if cc_addresses:
                msg['Cc'] = ', '.join(cc_addresses)
            if reply_to:
                msg['Reply-To'] = reply_to
            if in_reply_to:
                msg['In-Reply-To'] = in_reply_to
            if references:
                msg['References'] = references

            # Create body container
            body_container = MIMEMultipart('alternative')

            # Add text body
            if body_text:
                text_part = MIMEText(body_text, 'plain', 'utf-8')
                body_container.attach(text_part)

            # Add HTML body
            if body_html:
                html_part = MIMEText(body_html, 'html', 'utf-8')
                body_container.attach(html_part)

            # If no body provided, add default
            if not body_text and not body_html:
                text_part = MIMEText("", 'plain', 'utf-8')
                body_container.attach(text_part)

            # Attach body to main message
            msg.attach(body_container)

            # Add attachments
            if attachments:
                for attachment in attachments:
                    try:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(attachment['data'])
                        encoders.encode_base64(part)

                        filename = attachment.get('filename', 'attachment')
                        part.add_header(
                            'Content-Disposition',
                            f'attachment; filename= {filename}'
                        )

                        msg.attach(part)
                        logger.debug(f"Added attachment: {filename}")

                    except Exception as e:
                        logger.error(f"Error adding attachment {attachment.get('filename', 'unknown')}: {e}")

            # Collect all recipients
            all_recipients = to_addresses[:]
            if cc_addresses:
                all_recipients.extend(cc_addresses)
            if bcc_addresses:
                all_recipients.extend(bcc_addresses)

            # Send email
            self._server.send_message(msg, to_addrs=all_recipients)

            recipient_count = len(all_recipients)
            logger.info(f"Email sent successfully to {recipient_count} recipients via {self.config.name}")

            return {
                "success": True,
                "message": f"Email sent to {recipient_count} recipients",
                "recipients": recipient_count,
                "smtp_server": self.config.name
            }

        except Exception as e:
            error_msg = f"Failed to send email via {self.config.name}: {e}"
            logger.error(error_msg)
            return {"success": False, "message": error_msg}

    async def send_template_email(self,
                                 template_name: str,
                                 to_addresses: List[str],
                                 template_data: Dict,
                                 subject: Optional[str] = None,
                                 **kwargs) -> Dict[str, Union[bool, str]]:
        """
        Send email using a template.

        Args:
            template_name: Name of the template
            to_addresses: Recipient addresses
            template_data: Data to fill template placeholders
            subject: Email subject (can include template variables)
            **kwargs: Additional send_email arguments
        """
        try:
            # Simple template substitution (in production, use proper template engine)
            body_text = template_data.get('body_text', '')
            body_html = template_data.get('body_html', '')

            # Replace placeholders in templates
            for key, value in template_data.items():
                placeholder = f"{{{key}}}"
                if body_text:
                    body_text = body_text.replace(placeholder, str(value))
                if body_html:
                    body_html = body_html.replace(placeholder, str(value))
                if subject:
                    subject = subject.replace(placeholder, str(value))

            # Send the email
            return await self.send_email(
                to_addresses=to_addresses,
                subject=subject or "Email from Email Server",
                body_text=body_text if body_text else None,
                body_html=body_html if body_html else None,
                **kwargs
            )

        except Exception as e:
            error_msg = f"Failed to send template email: {e}"
            logger.error(error_msg)
            return {"success": False, "message": error_msg}

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()


class EmailSenderManager:
    """Manages multiple SMTP senders."""

    def __init__(self):
        self._senders = {}

    async def get_sender(self, smtp_config: SMTPConfig) -> EmailSender:
        """Get or create email sender for config."""
        sender_key = f"{smtp_config.id}_{smtp_config.host}"

        if sender_key not in self._senders:
            self._senders[sender_key] = EmailSender(smtp_config)

        return self._senders[sender_key]

    async def send_email_via_config(self, smtp_config_id: int, **email_args) -> Dict[str, Union[bool, str]]:
        """Send email using specific SMTP configuration."""
        from src.database.connection import get_db_session

        try:
            with get_db_session() as db:
                config = db.query(SMTPConfig).filter(SMTPConfig.id == smtp_config_id).first()
                if not config:
                    return {"success": False, "message": f"SMTP config {smtp_config_id} not found"}

                if not config.enabled:
                    return {"success": False, "message": f"SMTP config {config.name} is disabled"}

                # Create a detached config object with all needed attributes
                config_data = {
                    'id': config.id,
                    'name': config.name,
                    'account_name': config.account_name,
                    'host': config.host,
                    'port': config.port,
                    'smtp_port': getattr(config, 'smtp_port', 587),
                    'username': config.username,
                    'password': config.password,
                    'imap_use_ssl': getattr(config, 'imap_use_ssl', True),
                    'imap_use_tls': getattr(config, 'imap_use_tls', False),
                    'smtp_use_ssl': getattr(config, 'smtp_use_ssl', False),
                    'smtp_use_tls': getattr(config, 'smtp_use_tls', True),
                    'enabled': config.enabled
                }

            # Create a temporary config object outside the session
            class TempConfig:
                def __init__(self, data):
                    for key, value in data.items():
                        setattr(self, key, value)

            temp_config = TempConfig(config_data)
            sender = await self.get_sender(temp_config)
            return await sender.send_email(**email_args)

        except Exception as e:
            error_msg = f"Error sending email via config {smtp_config_id}: {e}"
            logger.error(error_msg)
            return {"success": False, "message": error_msg}

    def cleanup(self):
        """Disconnect all senders."""
        for sender in self._senders.values():
            sender.disconnect()
        self._senders.clear()


# Global sender manager instance
email_sender_manager = EmailSenderManager()