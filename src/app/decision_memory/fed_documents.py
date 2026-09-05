from __future__ import annotations

import hashlib
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


_OFFICIAL_FEDERAL_RESERVE_HOSTS = frozenset(
    {
        "federalreserve.gov",
        "atlantafed.org",
        "bostonfed.org",
        "chicagofed.org",
        "clevelandfed.org",
        "dallasfed.org",
        "kansascityfed.org",
        "minneapolisfed.org",
        "newyorkfed.org",
        "philadelphiafed.org",
        "richmondfed.org",
        "frbsf.org",
        "stlouisfed.org",
    }
)


def is_official_federal_reserve_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        hostname == official_host or hostname.endswith(f".{official_host}")
        for official_host in _OFFICIAL_FEDERAL_RESERVE_HOSTS
    )


def fetch_official_document(url: str) -> bytes:
    if not is_official_federal_reserve_url(url):
        raise ValueError("URL must be an official Federal Reserve HTTPS URL")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fomc-decision-memory/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class _ParagraphParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._parts: list[str] = []
        self.paragraphs: list[str] = []

    def _flush(self) -> None:
        normalized = " ".join("".join(self._parts).replace("\xa0", " ").split())
        if normalized:
            self.paragraphs.append(normalized)
        self._parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() == "p":
            if self._depth:
                self._flush()
            self._depth = 1
        elif tag.lower() == "br" and self._depth:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "p" or not self._depth:
            return
        self._depth = 0
        self._flush()


def extract_html_paragraphs(content: bytes) -> list[str]:
    parser = _ParagraphParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    return parser.paragraphs


class _ParagraphLineBlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._active = False
        self._line_parts: list[str] = []
        self._block: list[str] = []
        self.blocks: list[list[str]] = []

    def _flush_line(self) -> None:
        normalized = " ".join(
            "".join(self._line_parts).replace("\xa0", " ").split()
        )
        if normalized:
            self._block.append(normalized)
        self._line_parts = []

    def _flush_block(self) -> None:
        self._flush_line()
        if self._block:
            self.blocks.append(self._block)
        self._block = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.lower()
        if lowered == "p":
            if self._active:
                self._flush_block()
            self._active = True
        elif lowered == "br" and self._active:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._active:
            self._line_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._active:
            self._flush_block()
            self._active = False


def extract_html_paragraph_line_blocks(content: bytes) -> list[list[str]]:
    parser = _ParagraphLineBlockParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    if parser._active:
        parser._flush_block()
    return parser.blocks


def cache_official_document(
    url: str,
    output_path: Path,
    *,
    fetcher: Callable[[str], bytes] = fetch_official_document,
) -> dict[str, Any]:
    if not is_official_federal_reserve_url(url):
        raise ValueError("URL must be an official Federal Reserve HTTPS URL")
    resolved = output_path.resolve()
    if resolved.exists():
        raise FileExistsError(f"Refusing to overwrite cached document: {resolved}")
    content = fetcher(url)
    if not content:
        raise RuntimeError(f"Federal Reserve document is empty: {url}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("xb") as output:
        output.write(content)
    return {
        "source_url": url,
        "local_path": str(resolved),
        "byte_length": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
