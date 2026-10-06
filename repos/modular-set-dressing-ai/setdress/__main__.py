import sys

from . import project
from .core.cli import run
from .hooks import HOOKS


def main(argv=None) -> int:
    return run(project(), HOOKS, argv)


if __name__ == "__main__":
    sys.exit(main())
