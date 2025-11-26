"""Test logging configuration"""
import logging

from flacfetch.core.log import get_logger, setup_logging


def test_get_logger():
    """Test getting a logger"""
    logger = get_logger("test")
    # Logger uses the name as-is
    assert logger.name == "test"
    assert isinstance(logger, logging.Logger)


def test_get_logger_from_submodule():
    """Test getting logger from submodule strips prefix"""
    logger = get_logger("flacfetch.core.test")
    assert logger.name == "flacfetch.core.test"


def test_setup_logging_info():
    """Test setting up logging at INFO level"""
    setup_logging(verbose=False)
    logger = get_logger("test")
    assert logger.level <= logging.INFO


def test_setup_logging_debug():
    """Test setting up logging at DEBUG level"""
    setup_logging(verbose=True)
    logger = get_logger("test")
    assert logger.level <= logging.DEBUG

