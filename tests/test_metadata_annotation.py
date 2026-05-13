"""Tests for MetadataAnnotationService and build_annotation_prompt."""
import json
from unittest.mock import MagicMock, patch

import pytest

from prompts.annotation import build_annotation_prompt


def test_build_annotation_prompt_contains_all_inputs():
    prompt = build_annotation_prompt(
        domain="real_estate",
        domain_description="Real estate property listings — Toronto/Canada focus",
        table_name="raw_data",
        column_name="postal_code",
        sample_values=["M5V 2T6", "K1A 0A9"],
    )
    assert "real_estate" in prompt
    assert "Real estate property listings" in prompt
    assert "raw_data" in prompt
    assert "postal_code" in prompt
    assert "M5V 2T6" in prompt


def test_build_annotation_prompt_empty_samples_says_none():
    prompt = build_annotation_prompt("test", "Test domain", "raw_data", "ref_1", [])
    assert "ref_1" in prompt
    assert "none available" in prompt
