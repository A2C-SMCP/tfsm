"""Nox sessions for testing the tfsm library.

This file defines nox test sessions for multiple Python versions.
Usage:
    nox              # Run all sessions
    nox -s test      # Run test session only
    nox -s mypy      # Run type checking only
"""

import sys

import nox

# Python versions to test against (project requires 3.11+)
PYTHON_VERSIONS = ["3.11", "3.12", "3.13"]

# Nox options
nox.options.stop_on_first_error = False
nox.options.sessions = ["test", "mypy", "test_no_diagrams"]


@nox.session(python=False)  # Use system Python
def check_manifest(session: nox.Session) -> None:
    """Check package manifest for completeness."""
    session.install("check-manifest")
    # Ignore patterns moved from setup.cfg
    session.run("check-manifest", "--ignore", ".scrutinizer.yml,appveyor.yml")


@nox.session(python=False)  # Use system Python
def mypy(session: nox.Session) -> None:
    """Run strict mypy type checking."""
    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[dev,mypy]")

    # Run mypy strict checks
    session.run("uv", "run", "mypy", "--config-file", "mypy.ini", "--strict", "tfsm")

    # Also run codestyle tests
    session.run("uv", "run", "pytest", "tests/test_codestyle.py")


@nox.session(python=PYTHON_VERSIONS)
def test(session: nox.Session) -> None:
    """Run test suite with diagrams support."""
    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test,diagrams]")

    # Install graphviz system dependency (Linux only)
    if sys.platform.startswith("linux") and session.python == "3.13":
        session.run("sudo", "apt-get", "update", silent=True, external=True)
        session.run("sudo", "apt-get", "install", "-y", "graphviz", "libgraphviz-dev", silent=True, external=True)

    # Run pytest with auto-detection of parallel cores
    session.run("uv", "run", "pytest", "-nauto", "--doctest-modules", "tests/")


@nox.session(python=PYTHON_VERSIONS)
def test_no_diagrams(session: nox.Session) -> None:
    """Run test suite without diagrams (pure Python)."""
    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test]")

    # Run pytest
    session.run("uv", "run", "pytest", "-nauto", "tests/")


@nox.session(python=False)  # Use system Python
def coverage(session: nox.Session) -> None:
    """Run tests with coverage reporting."""
    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test,diagrams]")

    # Run pytest with coverage
    session.run("uv", "run", "pytest", "-nauto", "--cov=tfsm", "--cov-report=html", "--cov-report=term", "tests/")
