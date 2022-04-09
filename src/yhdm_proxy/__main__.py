import argparse
import trio
from . import serve


def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int, default=8787, nargs="?",
                        help="Specify alternate port [default: %(default)s]")
    parser.add_argument("-b", "--bind", metavar="ADDRESS", default="localhost",
                        help="Specify alternate bind address [default: %(default)s]")
    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    host = args.bind
    port = args.port

    try:
        trio.run(serve, host, port)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
