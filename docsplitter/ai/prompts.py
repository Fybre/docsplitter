"""Prompt templates for document boundary detection and classification."""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a document boundary detector. You analyse scanned pages from multi-document PDF batches.

Your task is to determine whether the CURRENT PAGE begins a NEW logical document or continues \
the previous one.

Respond ONLY with valid JSON — no markdown fences, no extra text — matching this schema exactly:
{
  "is_new_document": <boolean>,
  "document_type": <string or null>,
  "confidence": <float 0.0–1.0>,
  "reasoning": <string, max 80 words>
}

Rules:
- is_new_document: true if this page clearly starts a fresh, independent document
- document_type: short snake_case label (e.g. "invoice", "transcript", "letter"), or null if \
indeterminate
- confidence: your certainty — use < 0.7 when genuinely ambiguous
- reasoning: brief visual/textual evidence supporting your decision

A new document is indicated by things like: a new header/letterhead, a different date or \
reference number, a change in formatting style, a cover page, or a clear subject change.
A continuation is indicated by: sequential page numbers ("page 2 of 3"), continued mid-sentence \
text, or explicit "continued" markers.

IMPORTANT — same template ≠ continuation: Many documents in a batch share identical \
letterheads, logos, or form layouts (e.g. invoices from the same supplier). Do NOT treat \
visual similarity alone as evidence of continuation. Instead look for content signals: \
if the invoice number, document date, reference number, order number, or total amount \
differs from the previous page, that page is a NEW document, even if the layout looks \
identical. Same letterhead + different invoice number = new document.

IMPORTANT — remittance slip on an invoice: Many invoices include a detachable remittance \
slip or payment advice section printed at the bottom of the same page. This does NOT make \
the page a remittance advice — it is still an invoice. Only classify a page as \
remittance_advice if the entire page is a standalone remittance/payment confirmation with \
no invoice charges or line items.
"""

FIRST_PAGE_PROMPT = """\
This is the FIRST PAGE of the batch. It always starts a new document.

[image 1]{text_block}

Classify this page:
- document_type: identify the type of document
- confidence: your certainty in the document type classification
- reasoning: brief description of what you see

{type_hint_instruction}
"""

BOUNDARY_PROMPT = """\
Analyse the CURRENT PAGE to determine if it starts a new document.

{type_hint_instruction}

Is the CURRENT PAGE the start of a new document?
"""

TYPE_HINT_INSTRUCTION = """\
Expected document types for this channel: {hints}
Use one of these labels for document_type if it matches, otherwise use a descriptive label."""

NO_TYPE_HINT_INSTRUCTION = """\
Identify the document type freely — use a short, descriptive snake_case label."""


def build_type_hint_instruction(type_hints: list[str]) -> str:
    if type_hints:
        return TYPE_HINT_INSTRUCTION.format(hints=", ".join(type_hints))
    return NO_TYPE_HINT_INSTRUCTION


def build_first_page_prompt(type_hints: list[str], page_text: str = "") -> str:
    if page_text:
        excerpt = page_text[:500] + ("\n…\n" + page_text[-300:] if len(page_text) > 800 else "")
        text_block = f"\nExtracted text:\n{excerpt}"
    else:
        text_block = ""
    return FIRST_PAGE_PROMPT.format(
        text_block=text_block,
        type_hint_instruction=build_type_hint_instruction(type_hints),
    )


def build_boundary_prompt(type_hints: list[str]) -> str:
    return BOUNDARY_PROMPT.format(
        type_hint_instruction=build_type_hint_instruction(type_hints)
    )
