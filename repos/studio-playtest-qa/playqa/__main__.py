import sys

from . import project
from .guide_adapter import config
from .hooks import HOOKS


def main(argv=None) -> int:
    return config.run(project(), HOOKS, argv)


if __name__ == "__main__":
    sys.exit(main())
