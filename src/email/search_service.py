"""Search service using ripgrep for fast filesystem search.

Requirements:
    - ripgrep must be installed on the system: apt-get install ripgrep
    
    If ripgrep is not installed, search will return empty results with a warning log.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchMatch:
    """Single search match result."""
    email_id: int
    file_path: str
    matched_field: str
    preview: str
    line_number: int = 0


class SearchService:
    """Ripgrep-based search service with bash injection prevention."""

    MAX_PATTERN_LENGTH = 500
    MAX_RESULTS = 1000
    CONTEXT_LINES = 2

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or Path(settings.email_log_dir)

    def _validate_regex(self, pattern: str) -> bool:
        """
        Validate regex pattern for safety.
        
        Security measures:
        - No null bytes
        - Length limit
        - Valid regex syntax check
        """
        if not pattern:
            return False
        
        if '\x00' in pattern:
            logger.warning("Null byte detected in search pattern")
            return False
        
        if len(pattern) > self.MAX_PATTERN_LENGTH:
            logger.warning(f"Search pattern exceeds max length: {len(pattern)}")
            return False
        
        try:
            re.compile(pattern)
        except re.error as e:
            logger.warning(f"Invalid regex pattern: {e}")
            return False
        
        return True

    async def search(
        self,
        query: str,
        email_ids: Optional[List[int]] = None,
        fields: Optional[List[str]] = None,
        include_attachments: bool = False,
        limit: int = 50
    ) -> List[SearchMatch]:
        """
        Search emails using ripgrep.
        
        Args:
            query: Regex pattern to search for
            email_ids: Optional list of email IDs to restrict search
            fields: Which fields to search (subject, body, attachment)
            include_attachments: Whether to search attachment content
            limit: Maximum number of results
            
        Returns:
            List of SearchMatch objects
        """
        if not self._validate_regex(query):
            return []
        
        if not self.base_path.exists():
            logger.warning(f"Search path does not exist: {self.base_path}")
            return []
        
        limit = min(limit, self.MAX_RESULTS)
        
        try:
            matches = await self._rg_search(
                query=query,
                include_attachments=include_attachments,
                limit=limit
            )
            
            if email_ids:
                matches = self._filter_by_email_ids(matches, email_ids)
            
            if fields:
                matches = self._filter_by_fields(matches, fields)
            
            return matches[:limit]
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def _rg_search(
        self,
        query: str,
        include_attachments: bool,
        limit: int
    ) -> List[SearchMatch]:
        """Execute ripgrep search."""
        cmd = [
            "rg",
            "--json",
            "-e", query,
            "-C", str(self.CONTEXT_LINES),
            "--max-count", str(limit),
            str(self.base_path),
            "--glob", "*.txt"
        ]
        
        if not include_attachments:
            cmd.extend(["--glob", "!**/attachments/**"])
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            
            if result.returncode not in (0, 1):
                logger.warning(f"Ripgrep returned non-standard exit code: {result.returncode}")
            
            return self._parse_rg_output(result.stdout)
            
        except subprocess.TimeoutExpired:
            logger.warning("Ripgrep search timed out")
            return []
        except FileNotFoundError:
            logger.error("Ripgrep not found - install with: apt-get install ripgrep")
            return []
        except Exception as e:
            logger.error(f"Ripgrep execution failed: {e}")
            return []

    def _parse_rg_output(self, output: str) -> List[SearchMatch]:
        """Parse ripgrep JSON output."""
        matches = []
        
        for line in output.strip().split('\n'):
            if not line:
                continue
            
            try:
                data = json.loads(line)
                
                if data.get("type") != "match":
                    continue
                
                match_data = data.get("data", {})
                
                file_path = match_data.get("path", {}).get("text", "")
                if not file_path:
                    continue
                
                line_number = match_data.get("line_number", 0)
                matched_text = match_data.get("lines", {}).get("text", "").strip()
                
                email_id = self._extract_email_id(file_path)
                if email_id is None:
                    continue
                
                matched_field = self._determine_matched_field(file_path)
                
                matches.append(SearchMatch(
                    email_id=email_id,
                    file_path=file_path,
                    matched_field=matched_field,
                    preview=matched_text[:200],
                    line_number=line_number
                ))
                
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.debug(f"Error parsing rg output line: {e}")
                continue
        
        return matches

    def _extract_email_id(self, file_path: str) -> Optional[int]:
        """Extract email ID from file path."""
        try:
            path = Path(file_path)
            filename = path.stem
            
            parts = filename.split('_')
            for part in reversed(parts):
                if part.isdigit():
                    return int(part)
            
            return None
        except Exception:
            return None

    def _determine_matched_field(self, file_path: str) -> str:
        """Determine which field was matched based on file path."""
        path = Path(file_path)
        
        if "attachments" in path.parts:
            return "attachment"
        
        if path.suffix == ".meta.json":
            return "metadata"
        
        return "body"

    def _filter_by_email_ids(
        self,
        matches: List[SearchMatch],
        email_ids: List[int]
    ) -> List[SearchMatch]:
        """Filter matches to only include specified email IDs."""
        email_id_set = set(email_ids)
        return [m for m in matches if m.email_id in email_id_set]

    def _filter_by_fields(
        self,
        matches: List[SearchMatch],
        fields: List[str]
    ) -> List[SearchMatch]:
        """Filter matches by matched field."""
        field_set = set(fields)
        return [m for m in matches if m.matched_field in field_set]

    async def search_metadata(
        self,
        query: str,
        smtp_config_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 50
    ) -> List[SearchMatch]:
        """
        Search metadata files for specific criteria.
        """
        if not self._validate_regex(query):
            return []
        
        cmd = [
            "rg",
            "--json",
            "-e", query,
            "-l",
            str(self.base_path),
            "--glob", "*.meta.json"
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False
            )
            
            return self._parse_rg_output(result.stdout)
            
        except Exception as e:
            logger.error(f"Metadata search failed: {e}")
            return []


class RegexSearchHelper:
    """Helper for regex pattern building."""
    
    @staticmethod
    def build_sender_pattern(email: str) -> str:
        """Build regex to match sender email."""
        escaped = re.escape(email)
        return f"From:.*{escaped}"
    
    @staticmethod
    def build_subject_pattern(subject: str) -> str:
        """Build regex to match subject."""
        escaped = re.escape(subject)
        return f"Subject:.*{escaped}"
    
    @staticmethod
    def build_date_pattern(date_from: datetime, date_to: Optional[datetime] = None) -> str:
        """Build regex to match date range."""
        from_str = date_from.strftime(r"%Y-%m-%d")
        if date_to:
            to_str = date_to.strftime(r"%Y-%m-%d")
            return f"Date:.*{from_str}.*{to_str}"
        return f"Date:.*{from_str}"
