"""Shared server-side PDF rendering helpers.

``budget/pdf.py`` and ``billing/pdf.py`` each grew their own copy of the
same three primitives (WeasyPrint invocation, clinic address flattening,
language → money-locale mapping). This module is the single home for
them; new PDF surfaces should import from here rather than copy again.

The two existing generators still carry their private copies — migrating
them is a separate, riskier change and is deliberately out of scope here.
"""

from __future__ import annotations

import asyncio
from io import BytesIO

# Language code (UI locale) → POSIX locale used for money formatting.
# Bolivia-first: `es` renders Bs with the local separators.
_MONEY_LOCALE_BY_LANG = {"es": "es_BO", "en": "en_US", "fr": "fr_FR"}


def money_locale(lang: str) -> str:
    """Map a UI language code to the POSIX locale for currency output."""
    return _MONEY_LOCALE_BY_LANG.get(lang, "es_BO")


def format_address(address: dict | None) -> str:
    """Flatten a clinic/patient address dict into one readable line."""
    if not address:
        return ""
    parts = []
    if address.get("street"):
        parts.append(address["street"])
    city_line = " ".join(filter(None, [address.get("postal_code"), address.get("city")]))
    if city_line:
        parts.append(city_line)
    if address.get("country"):
        parts.append(address["country"])
    return ", ".join(parts)


def _render(html_content: str) -> bytes:
    try:
        from weasyprint import HTML
    except ImportError:
        # WeasyPrint absent (some dev machines). Returning the HTML keeps
        # the caller working instead of 500-ing; production images ship it.
        return html_content.encode("utf-8")

    buffer = BytesIO()
    HTML(string=html_content).write_pdf(buffer)
    return buffer.getvalue()


async def html_to_pdf(html_content: str) -> bytes:
    """Render HTML to PDF bytes off the event loop.

    WeasyPrint is CPU-bound and can take hundreds of milliseconds, which
    would otherwise stall every other request on the worker.
    """
    return await asyncio.to_thread(_render, html_content)
