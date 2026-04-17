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

    sub.add_parser("tui",  help="Run the TUI (default).")
    sub.add_parser("mcp",  help="Run the MCP server on stdio.")
    sub.add_parser("init", help="Scaffold ~/.config/servertui/.")

    args = parser.parse_args(argv)

    if args.cmd in (None, "tui"):
        from servertui.tui import ServerTUI
        ServerTUI().run()
        return 0

    if args.cmd == "mcp":
        from servertui.mcp import mcp
        mcp.run()
        return 0

    if args.cmd == "init":
        from servertui.init import run_init
        return run_init()

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
