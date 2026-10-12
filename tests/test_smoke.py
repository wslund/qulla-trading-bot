"""Smoke tests: the package and its subpackages are installed and importable."""

import importlib

import pytest

import tradingbot

SUBPACKAGES = ["data", "scanner", "backtesting", "risk"]


def test_version_is_set() -> None:
    assert tradingbot.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"tradingbot.{name}")
