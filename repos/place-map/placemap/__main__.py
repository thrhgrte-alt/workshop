import sys

from . import project
from .guide_adapter import cli
from .hooks import HOOKS

run = cli.run


def main(argv=None) -> int:
    return run(project(), HOOKS, argv)


if __name__ == "__main__":
    sys.exit(main())
