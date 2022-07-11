import sys

import trio
import yhdm_proxy


def run():
    try:
        trio.run(yhdm_proxy.serve, "localhost", 8787)
    except KeyboardInterrupt:
        pass


def yapii_run(clock):
    yappi.set_clock_type(clock)
    with yappi.run():
        run()
    yappi.get_func_stats().save("yappi.prof", "pstat")
    with open("yappi_prof.txt", "w", encoding="utf-8") as f:
        yappi.get_func_stats().print_all(f)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("cpu", "wall", "justrun"):
        clock = sys.argv[1]
    else:
        print(f"python {sys.argv[0]} <cpu | wall | justrun>", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "justrun":
        run()
    else:
        yappi_run(clock)


if __name__ == "__main__":
    main()
