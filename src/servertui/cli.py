"""servertui CLI — subcommand router."""
import argparse
import sys

from servertui import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="servertui",
        description="Terminal dashboard for local server infrastructure.",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"servertui {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    # Subcommands will be wired in subsequent tasks.

    parser.parse_args(argv)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
