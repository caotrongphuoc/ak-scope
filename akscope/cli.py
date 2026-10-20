import argparse

from akscope import __version__, commands


def build_parser():
    parser = argparse.ArgumentParser(
        prog="akscope",
        description="record, visualize and verify AK firmware message flow",
    )
    parser.add_argument("--version", action="version", version="akscope " + __version__)
    subparsers = parser.add_subparsers(dest="command", metavar="command")
    commands.register(subparsers)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)
