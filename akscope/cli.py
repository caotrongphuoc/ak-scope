import argparse

from akscope import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="akscope",
        description="record, visualize and verify AK firmware message flow",
    )
    parser.add_argument("--version", action="version", version="akscope " + __version__)
    # each command module registers its own subparser here
    parser.add_subparsers(dest="command", metavar="command")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)
