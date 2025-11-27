"""
Core business logic for swim coaching.

This module is framework-agnostic - it doesn't import FastAPI, Snowflake,
or any infrastructure concerns. This separation means we can test the
coaching logic in isolation and swap frameworks if needed.
"""

