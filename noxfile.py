import nox
import os

PYTHON_VERSIONS = ["3.12"]
PYTHON_DEFAULT_VERSION = PYTHON_VERSIONS[-1]

CODE_LOCATIONS = ["src"]

os.environ["PDM_IGNORE_SAVED_PYTHON"] = "1"

nox.options.stop_on_first_error = True

@nox.session(name="format", python=PYTHON_DEFAULT_VERSION)
def format(session):
    """Lint the code and apply fixes in-place whenever possible."""
    session.install("ruff")
    session.run("ruff", "check", "--fix", *CODE_LOCATIONS)
    session.run("ruff", "format", *CODE_LOCATIONS)

@nox.session(name="lint", python=PYTHON_DEFAULT_VERSION)
def lint(session):
    """Run linters in readonly mode."""
    session.install("ruff", "codespell")
    session.run("ruff", "check", "--diff", "--unsafe-fixes", *CODE_LOCATIONS)
    session.run("codespell", *CODE_LOCATIONS)
    session.run("ruff", "format", "--diff", *CODE_LOCATIONS)

@nox.session(name="typecheck", python=PYTHON_DEFAULT_VERSION)
def typecheck(session):
    session.run_always('pdm', 'install', '-G', 'test', external=True)
    session.install("mypy")
    session.run("mypy", "--explicit-package-bases", *CODE_LOCATIONS)

@nox.session(name="tests", python=PYTHON_DEFAULT_VERSION)
def tests(session):
    """Run tests with pytest."""
    session.run_always('pdm', 'install', '-G', 'test', external=True)
    session.install("-e", ".")
    session.install("pytest")
    session.run("pytest", "--tb=short", *CODE_LOCATIONS)
