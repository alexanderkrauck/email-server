"""Convert emails to readable Markdown format."""

import re
import html
from datetime import datetime
from typing import Dict
from bs4 import BeautifulSoup


class EmailToMarkdownConverter:
    """Converts email content to clean Markdown format."""

    def __init__(self):
        pass

    def convert_email_to_markdown(self, email_data: Dict) -> str:
        """Convert email data to markdown format."""
        try:
            md_content = []

            # Header
            md_content.append("# Email")
            md_content.append("")

            # Metadata table
            md_content.append("## Metadata")
            md_content.append("")
            md_content.append("| Field | Value |")
            md_content.append("|-------|-------|")
            md_content.append(f"| **From** | {self._escape_markdown(email_data.get('sender', ''))} |")
            md_content.append(f"| **To** | {self._escape_markdown(email_data.get('recipient', ''))} |")
            md_content.append(f"| **Subject** | {self._escape_markdown(email_data.get('subject', ''))} |")

            if email_data.get('email_date'):
                date_str = email_data['email_date'].strftime('%Y-%m-%d %H:%M:%S UTC') if hasattr(email_data['email_date'], 'strftime') else str(email_data['email_date'])
                md_content.append(f"| **Date** | {date_str} |")

            md_content.append(f"| **Message ID** | `{email_data.get('message_id', '')}` |")
            md_content.append(f"| **Size** | {email_data.get('content_size', 0)} bytes |")

            if email_data.get('attachment_count', 0) > 0:
                md_content.append(f"| **Attachments** | {email_data.get('attachment_count', 0)} |")

            md_content.append("")

            # Body content
            body_plain = email_data.get('body_plain', '')
            body_html = email_data.get('body_html', '')

            if body_html:
                md_content.append("## Content")
                md_content.append("")
                html_to_md = self._html_to_markdown(body_html)
                md_content.append(html_to_md)
            elif body_plain:
                md_content.append("## Content")
                md_content.append("")
                # Format plain text nicely
                formatted_plain = self._format_plain_text(body_plain)
                md_content.append(formatted_plain)

            # Attachments section
            if hasattr(email_data, 'attachments') and email_data.attachments:
                md_content.append("")
                md_content.append("## Attachments")
                md_content.append("")
                for i, attachment in enumerate(email_data.attachments, 1):
                    md_content.append(f"### {i}. {attachment.filename}")
                    md_content.append("")
                    md_content.append(f"- **Type**: {attachment.content_type or 'unknown'}")
                    md_content.append(f"- **Size**: {self._format_file_size(attachment.size)}")
                    md_content.append("")

            md_content.append("---")
            md_content.append(f"*Processed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}*")

            return '\n'.join(md_content)

        except Exception as e:
            # Fallback to basic format
            return f"# Email Processing Error\n\nFailed to convert email to markdown: {str(e)}\n\n" + \
                   f"**From**: {email_data.get('sender', '')}\n" + \
                   f"**Subject**: {email_data.get('subject', '')}\n"

    def _html_to_markdown(self, html_content: str) -> str:
        """Convert HTML content to Markdown."""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Handle common HTML elements
            self._convert_headers(soup)
            self._convert_links(soup)
            self._convert_bold_italic(soup)
            self._convert_lists(soup)
            self._convert_tables(soup)
            self._convert_images(soup)
            self._convert_code(soup)
            self._convert_quotes(soup)

            # Get text and clean up
            text = soup.get_text()
            text = self._clean_whitespace(text)

            return text

        except Exception:
            # Fallback: strip HTML tags
            return self._strip_html_tags(html_content)

    def _convert_headers(self, soup):
        """Convert HTML headers to Markdown."""
        for i in range(1, 7):
            for tag in soup.find_all(f'h{i}'):
                tag.string = f"{'#' * i} {tag.get_text().strip()}\n\n"

    def _convert_links(self, soup):
        """Convert HTML links to Markdown."""
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.get_text().strip()
            if href and text:
                link.string = f"[{text}]({href})"

    def _convert_bold_italic(self, soup):
        """Convert bold/italic to Markdown."""
        for tag in soup.find_all(['strong', 'b']):
            tag.string = f"**{tag.get_text()}**"

        for tag in soup.find_all(['em', 'i']):
            tag.string = f"*{tag.get_text()}*"

    def _convert_lists(self, soup):
        """Convert HTML lists to Markdown."""
        for ul in soup.find_all('ul'):
            items = []
            for li in ul.find_all('li'):
                items.append(f"- {li.get_text().strip()}")
            ul.string = '\n'.join(items) + '\n\n'

        for ol in soup.find_all('ol'):
            items = []
            for i, li in enumerate(ol.find_all('li'), 1):
                items.append(f"{i}. {li.get_text().strip()}")
            ol.string = '\n'.join(items) + '\n\n'

    def _convert_tables(self, soup):
        """Convert HTML tables to Markdown (basic)."""
        for table in soup.find_all('table'):
            rows = []
            for tr in table.find_all('tr'):
                cells = [td.get_text().strip() for td in tr.find_all(['td', 'th'])]
                if cells:
                    rows.append('| ' + ' | '.join(cells) + ' |')

            if rows:
                # Add header separator after first row
                if len(rows) > 1:
                    separator = '|' + '|'.join(['---'] * len(rows[0].split('|')[1:-1])) + '|'
                    rows.insert(1, separator)
                table.string = '\n'.join(rows) + '\n\n'

    def _convert_images(self, soup):
        """Convert HTML images to Markdown."""
        for img in soup.find_all('img'):
            src = img.get('src', '')
            alt = img.get('alt', 'Image')
            if src:
                img.string = f"![{alt}]({src})"

    def _convert_code(self, soup):
        """Convert code elements to Markdown."""
        for code in soup.find_all('code'):
            code.string = f"`{code.get_text()}`"

        for pre in soup.find_all('pre'):
            pre.string = f"```\n{pre.get_text()}\n```\n"

    def _convert_quotes(self, soup):
        """Convert blockquotes to Markdown."""
        for quote in soup.find_all('blockquote'):
            text = quote.get_text().strip()
            quoted = '\n'.join(f"> {line}" for line in text.split('\n'))
            quote.string = f"{quoted}\n\n"

    def _strip_html_tags(self, html_content: str) -> str:
        """Fallback: strip HTML tags."""
        # Remove HTML tags
        clean = re.sub('<.*?>', '', html_content)
        # Decode HTML entities
        clean = html.unescape(clean)
        return self._clean_whitespace(clean)

    def _format_plain_text(self, text: str) -> str:
        """Format plain text for better readability."""
        # Preserve intentional line breaks but clean up excessive whitespace
        lines = text.split('\n')
        formatted_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped:
                formatted_lines.append(stripped)
            else:
                # Preserve single empty lines
                if formatted_lines and formatted_lines[-1] != '':
                    formatted_lines.append('')

        return '\n'.join(formatted_lines)

    def _clean_whitespace(self, text: str) -> str:
        """Clean up excessive whitespace."""
        # Replace multiple spaces with single space
        text = re.sub(r' +', ' ', text)
        # Replace multiple newlines with double newline
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        return text.strip()

    def _escape_markdown(self, text: str) -> str:
        """Escape Markdown special characters."""
        if not text:
            return ""
        # Escape markdown special characters
        special_chars = r'[\*\_\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.!]'
        return re.sub(special_chars, r'\\\g<0>', text)

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"