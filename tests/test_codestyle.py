import subprocess
import unittest
from os.path import exists

import pycodestyle

try:
    import mypy
except ImportError:
    mypy = None  # type: ignore


class TestCodeFormat(unittest.TestCase):
    def test_conformance(self):
        """Test that we conform to PEP-8."""
        style = pycodestyle.StyleGuide(quiet=False, ignore=["E203", "E501", "W605", "W503"])
        if exists("tfsm"):  # when run from root directory (e.g. tox)
            style.input_dir("tfsm")
            style.input_dir("tests")
        else:  # when run from test directory (e.g. pycharm)
            style.input_dir("../tfsm")
            style.input_dir(".")
        result = style.check_files()
        self.assertEqual(result.total_errors, 0, "Found code style errors (and warnings).")

    @unittest.skipIf(mypy is None, "mypy not found")
    def test_mypy_package(self):
        call = ["mypy", "--config-file", "mypy.ini", "--strict", "tfsm", "tests/test_imports.py"]

        # when run from root directory (e.g. tox) else when run from test directory (e.g. pycharm)
        project_root = "." if exists("tfsm") else ".."
        subprocess.check_call(call, cwd=project_root)

    @unittest.skipIf(mypy is None, "mypy not found")
    def test_mypy_tests(self):
        call = ["mypy", "tests", "--disable-error-code", "attr-defined", "--disable-error-code", "no-untyped-def"]
        # when run from root directory (e.g. tox) else when run from test directory (e.g. pycharm)
        project_root = "." if exists("tfsm") else ".."
        subprocess.check_call(call, cwd=project_root)
