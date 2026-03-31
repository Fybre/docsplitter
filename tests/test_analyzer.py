"""Tests for the AI analyzer — mocks the AI client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from docsplitter.ai.analyzer import PageAnalyzer, _build_boundaries
from docsplitter.models import PageAnalysisResult


def _result(page: int, is_new: bool, doc_type: str | None, conf: float) -> PageAnalysisResult:
    return PageAnalysisResult(
        page_index=page,
        is_new_document=is_new,
        document_type=doc_type,
        confidence=conf,
        reasoning="test",
    )


class TestBuildBoundaries:
    def test_single_page(self):
        results = [_result(0, True, "invoice", 0.9)]
        boundaries = _build_boundaries(results)
        assert len(boundaries) == 1
        assert boundaries[0].start_page == 0
        assert boundaries[0].end_page == 0
        assert boundaries[0].document_type == "invoice"

    def test_no_splits(self):
        results = [
            _result(0, True, "invoice", 0.9),
            _result(1, False, None, 0.85),
            _result(2, False, None, 0.88),
        ]
        boundaries = _build_boundaries(results)
        assert len(boundaries) == 1
        assert boundaries[0].start_page == 0
        assert boundaries[0].end_page == 2
        assert abs(boundaries[0].avg_confidence - (0.9 + 0.85 + 0.88) / 3) < 0.001

    def test_two_documents(self):
        results = [
            _result(0, True, "invoice", 0.95),
            _result(1, False, None, 0.90),
            _result(2, True, "letter", 0.88),
            _result(3, False, None, 0.82),
        ]
        boundaries = _build_boundaries(results)
        assert len(boundaries) == 2
        assert boundaries[0].start_page == 0
        assert boundaries[0].end_page == 1
        assert boundaries[0].document_type == "invoice"
        assert boundaries[1].start_page == 2
        assert boundaries[1].end_page == 3
        assert boundaries[1].document_type == "letter"

    def test_type_inherited_from_first_page(self):
        """Pages with None type inherit from the boundary start page."""
        results = [
            _result(0, True, "transcript", 0.9),
            _result(1, False, None, 0.8),
            _result(2, False, None, 0.7),
        ]
        boundaries = _build_boundaries(results)
        assert boundaries[0].document_type == "transcript"

    def test_empty(self):
        assert _build_boundaries([]) == []

    def test_every_page_new(self):
        results = [_result(i, True, "invoice", 0.9) for i in range(5)]
        boundaries = _build_boundaries(results)
        assert len(boundaries) == 5
        for i, b in enumerate(boundaries):
            assert b.start_page == i
            assert b.end_page == i


class TestPageAnalyzer:
    @pytest.mark.asyncio
    async def test_analyse_single_page(self, tmp_path: Path):
        mock_client = MagicMock()
        mock_client.model = "test-model"
        mock_client.analyse_page = AsyncMock(
            return_value={
                "is_new_document": True,
                "document_type": "invoice",
                "confidence": 0.92,
                "reasoning": "Clear invoice header",
            }
        )

        analyzer = PageAnalyzer(mock_client)
        # Create a dummy image file
        img = tmp_path / "page_0000.jpg"
        img.write_bytes(b"fake-image-data")

        plan = await analyzer.analyse(
            source_file="/tmp/test.pdf",
            page_images=[img],
            type_hints=["invoice"],
        )

        assert plan.total_pages == 1
        assert len(plan.boundaries) == 1
        assert plan.boundaries[0].document_type == "invoice"
        assert plan.min_confidence == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_analyse_multiple_pages_with_split(self, tmp_path: Path):
        call_count = 0

        async def fake_analyse(**kwargs):
            nonlocal call_count
            call_count += 1
            # First call = page 0 (classification only)
            # Second call = page 1 boundary check → new document
            if call_count == 1:
                return {"document_type": "invoice", "confidence": 0.95, "reasoning": "invoice"}
            elif call_count == 2:
                return {
                    "is_new_document": True,
                    "document_type": "letter",
                    "confidence": 0.88,
                    "reasoning": "letterhead",
                }
            else:
                return {
                    "is_new_document": False,
                    "document_type": None,
                    "confidence": 0.91,
                    "reasoning": "continued",
                }

        mock_client = MagicMock()
        mock_client.model = "test-model"
        mock_client.analyse_page = AsyncMock(side_effect=fake_analyse)

        imgs = []
        for i in range(3):
            img = tmp_path / f"page_{i:04d}.jpg"
            img.write_bytes(b"fake")
            imgs.append(img)

        analyzer = PageAnalyzer(mock_client)
        plan = await analyzer.analyse("/tmp/test.pdf", imgs, [])

        assert len(plan.boundaries) == 2
        assert plan.boundaries[0].document_type == "invoice"
        assert plan.boundaries[1].document_type == "letter"
        assert plan.boundaries[0].start_page == 0
        assert plan.boundaries[0].end_page == 0
        assert plan.boundaries[1].start_page == 1
        assert plan.boundaries[1].end_page == 2

    @pytest.mark.asyncio
    async def test_failed_page_gets_zero_confidence(self, tmp_path: Path):
        mock_client = MagicMock()
        mock_client.model = "test-model"
        mock_client.analyse_page = AsyncMock(side_effect=ValueError("API error"))

        imgs = []
        for i in range(2):
            img = tmp_path / f"page_{i:04d}.jpg"
            img.write_bytes(b"fake")
            imgs.append(img)

        analyzer = PageAnalyzer(mock_client)
        plan = await analyzer.analyse("/tmp/test.pdf", imgs, [])

        # Should not raise; should produce plan with 0.0 confidence
        assert plan.min_confidence == 0.0
        assert len(plan.boundaries) >= 1
