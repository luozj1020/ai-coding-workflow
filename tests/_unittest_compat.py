"""Run function tests under the repository's standard-library test runner."""

from __future__ import annotations

import inspect
import re
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


@contextmanager
def raises(expected_exception, match: Optional[str] = None):
    try:
        yield
    except expected_exception as exc:
        if match is not None and re.search(match, str(exc)) is None:
            raise AssertionError(f"{str(exc)!r} does not match {match!r}") from exc
    else:
        raise AssertionError(f"{expected_exception.__name__} was not raised")


def skip(reason: str) -> None:
    raise unittest.SkipTest(reason)


def load_function_tests(namespace: dict) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for name, function in sorted(namespace.items()):
        if not name.startswith("test_") or not inspect.isfunction(function):
            continue
        parameters = tuple(inspect.signature(function).parameters)
        if parameters not in ((), ("tmp_path",)):
            raise TypeError(f"unsupported unittest function signature: {name}{parameters}")

        def run(function=function, parameters=parameters):
            if parameters:
                with tempfile.TemporaryDirectory() as directory:
                    function(Path(directory))
            else:
                function()

        suite.addTest(unittest.FunctionTestCase(run, description=name))
    return suite
