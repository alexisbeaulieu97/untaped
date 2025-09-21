"""Logging configuration for untaped-github package."""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict

from loguru import logger


def configure_logging(
    level: str = "INFO", format_string: str = None, enable_json: bool = False
) -> None:
    """Configure structured logging for the untaped-github package.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Custom format string for log messages
        enable_json: Enable JSON structured logging
    """
    # Remove default handlers
    logger.remove()

    # Set log level
    log_level = getattr(logging, level.upper(), logging.INFO)

    if enable_json:
        # JSON structured logging for production
        format_string = format_string or (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}"
        )
        logger.add(sys.stdout, level=log_level, format=format_string, serialize=True, enqueue=True)
    else:
        # Human-readable logging for development
        format_string = format_string or (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )
        logger.add(sys.stdout, level=log_level, format=format_string, colorize=True, enqueue=True)

    # Add file logging for errors
    logger.add(
        "logs/github-operations.log",
        level="WARNING",
        rotation="10 MB",
        retention="1 week",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
    )

    # Configure specific logger for GitHub operations
    github_logger = logger.bind(service="untaped-github")

    # Log configuration
    github_logger.info(f"Logging configured with level {level}")


def get_logger(name: str = "untaped-github") -> Any:
    """Get a logger instance for the untaped-github package.

    Args:
        name: Logger name (typically the module name)

    Returns:
        Loguru logger instance
    """
    return logger.bind(module=name)


# Common log messages
LOG_MESSAGES = {
    "config_loaded": "Configuration loaded from {config_path}",
    "variables_loaded": "Variables loaded from {vars_path}",
    "template_rendered": "Configuration template rendered successfully",
    "validation_passed": "Configuration validation passed",
    "validation_failed": "Configuration validation failed: {errors}",
    "authentication_success": "GitHub CLI authentication verified",
    "authentication_failed": "GitHub CLI authentication failed: {error}",
    "api_call_start": "GitHub API call started: {operation}",
    "api_call_success": "GitHub API call completed: {operation}",
    "api_call_failed": "GitHub API call failed: {operation} - {error}",
    "file_read_success": "File read successfully: {repository}/{file_path}",
    "directory_list_success": "Directory listing completed: {repository}/{directory_path}",
    "dry_run_success": "Dry run completed successfully",
    "dry_run_failed": "Dry run failed: {error}",
}
