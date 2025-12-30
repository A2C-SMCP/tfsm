"""Nox sessions for testing the tfism library.

This file defines nox test sessions for multiple Python versions.
Usage:
    nox              # Run all sessions
    nox -s test      # Run test session only
    nox -s mypy      # Run type checking only
"""

import sys

import nox

# Python versions to test against (project requires 3.10+)
PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]

# Nox options
nox.options.stop_on_first_error = False
nox.options.sessions = ["test", "mypy", "test_no_diagrams"]
nox.options.default_venv_backend = "uv"  # Use uv for better Python discovery


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
    session.run("uv", "run", "mypy", "--config-file", "mypy.ini", "--strict", "tfism")

    # Also run codestyle tests
    session.run("uv", "run", "pytest", "tests/test_codestyle.py")


@nox.session(python=PYTHON_VERSIONS)
def test(session: nox.Session) -> None:
    """Run test suite with diagrams support."""
    # Set graphviz paths for macOS (brew installation)
    if sys.platform == "darwin":
        graphviz_prefix = session.run("brew", "--prefix", "graphviz", silent=True, external=True).strip()
        session.env["CFLAGS"] = f"-I{graphviz_prefix}/include"
        session.env["LDFLAGS"] = f"-L{graphviz_prefix}/lib"

    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test,diagrams]")

    # Install graphviz system dependency (Linux only)
    if sys.platform.startswith("linux") and session.python == "3.13":
        session.run("sudo", "apt-get", "update", silent=True, external=True)
        session.run("sudo", "apt-get", "install", "-y", "graphviz", "libgraphviz-dev", silent=True, external=True)

    # Run pytest with auto-detection of parallel cores
    # Use session.bin to ensure we use the nox venv, not project .venv
    session.run(f"{session.bin}/pytest", "-nauto", "--doctest-modules", "tests/")


@nox.session(python=PYTHON_VERSIONS)
def test_no_diagrams(session: nox.Session) -> None:
    """Run test suite without diagrams (pure Python)."""
    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test]")

    # Run pytest
    session.run(f"{session.bin}/pytest", "-nauto", "tests/")


@nox.session(python=False)  # Use system Python
def coverage(session: nox.Session) -> None:
    """Run tests with coverage reporting."""
    # Set graphviz paths for macOS (brew installation)
    if sys.platform == "darwin":
        graphviz_prefix = session.run("brew", "--prefix", "graphviz", silent=True, external=True).strip()
        session.env["CFLAGS"] = f"-I{graphviz_prefix}/include"
        session.env["LDFLAGS"] = f"-L{graphviz_prefix}/lib"

    # Install project dependencies using uv
    session.run("uv", "pip", "install", "-e", ".[test,diagrams]")

    # Run pytest with coverage
    # Note: coverage session uses system Python, so we use uv run
    session.run("uv", "run", "pytest", "-nauto", "--cov=tfism", "--cov-report=html", "--cov-report=term", "tests/")
