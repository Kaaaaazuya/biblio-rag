import sys

from .chunk import _cli

if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
