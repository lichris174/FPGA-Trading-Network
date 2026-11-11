"""Replay a day of ITCH into the order book on the Tang Nano 20K and check it against the model.

    python scripts/board_replay.py FILE SYMBOL [--port COM9] [--baud 460800] [--every 10000]

Needs synth/book_board_top.v loaded on the board and pyserial
(pip install -e ".[board]"). The board's book is reset and its level
window centered on the 9:30 mid (or the last mid, for a file that ends
before the open). Messages go over the USB serial port; after every
`--every` messages the board reports its top 2 and flags, which must
equal golden/hw_book.py's at that point. The first difference stops the
run. At the end: the board's latency per book message, in clocks of the
54 MHz board clock and in nanoseconds.
"""

import argparse
import sys
import time

import serial

from board_link import REPORT_REQUEST, ByteSums, config, frame, mismatch, parse_report
from book_stats import opening_mid
from golden.hw_book import HwBook, window_base
from golden.itch.parser import decode
from golden.itch.tools import filter_stock

CLOCK_HZ = 54_000_000


def find_locate(path, symbol):
    for raw in filter_stock(path, symbol):
        if chr(raw[0]) == "R":
            m = decode(raw)
            if m["stock"] == symbol:
                return m["stock_locate"]
    raise SystemExit(f"no stock directory message for {symbol}")


RETRIES = 10
bad_lines = 0
sent_sums = ByteSums()         # every byte sent since the book reset, as the board counts them


def send(port, data):
    port.write(data)
    sent_sums.add(data)


def read_report(port, timeout):
    """Read a report line. The USB serial bridge sometimes drops characters
    on the way back above 115200 baud; a report request can be repeated, so
    a damaged line is simply asked for again."""
    global bad_lines
    port.timeout = timeout
    for _ in range(RETRIES):
        line = port.readline().decode("ascii", "replace")
        if not line:
            raise SystemExit("no report from the board (is synth/book_board_top.v loaded?)")
        try:
            return parse_report(line)
        except ValueError:
            bad_lines += 1
            send(port, REPORT_REQUEST)
    raise SystemExit(f"{RETRIES} damaged report lines in a row, last {line!r}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python scripts/board_replay.py")
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("--port", default="COM9")
    p.add_argument("--baud", type=int, default=460_800)
    p.add_argument("--every", type=int, default=10_000, help="messages between reports")
    p.add_argument("--limit", type=int, default=0, help="stop after this many messages (0 = all)")
    p.add_argument("--fine-from", type=int, default=0,
                   help="from this message on, check after every message (to find the first wrong one)")
    p.add_argument("--settle", type=float, default=0.0,
                   help="extra seconds to wait after a chunk has gone out before asking for the report")
    a = p.parse_args(argv)

    center = opening_mid(a.file, a.symbol)
    locate = find_locate(a.file, a.symbol)
    model = HwBook(center)
    print(f"{a.file}: {a.symbol} locate {locate}, window centered on ${center / 1e4:.2f}")

    with serial.Serial(a.port, a.baud, timeout=10, write_timeout=None) as port:
        port.reset_input_buffer()
        port.write(config(window_base(center), locate))
        send(port, REPORT_REQUEST)              # the board counts from here: the reset ends the config frame
        r = read_report(port, 10)
        if not r["ready"] or r["n"] != 0:
            raise SystemExit(f"board did not come up clean: {r}")

        start, sent, msgs, n, reports = time.time(), 0, 0, 0, 0
        chunk = []
        for raw in filter_stock(a.file, a.symbol):
            chunk.append(frame(raw))
            n += model.apply(decode(raw))
            msgs += 1
            last = a.limit and msgs >= a.limit
            fine = a.fine_from and msgs >= a.fine_from
            if len(chunk) == a.every or last or fine:
                data = b"".join(chunk)
                send(port, data)
                port.flush()
                if a.settle:
                    time.sleep(len(data) * 10 / a.baud + a.settle)
                send(port, REPORT_REQUEST)
                sent += len(data) + len(REPORT_REQUEST)
                chunk = []
                r = read_report(port, 30)
                reports += 1
                if r["rx_sums"] != (sent_sums.s1, sent_sums.s2):
                    print(f"\nLINK ERROR after {msgs:,} messages: the board received different bytes "
                          f"(board {r['rx_sums']}, sent {(sent_sums.s1, sent_sums.s2)})")
                    return 2
                diff = mismatch(r, model, n)
                if diff:
                    print(f"MISMATCH after {msgs:,} messages ({n:,} book messages):\n{diff}")
                    if fine:
                        print(f"message {msgs:,}: {decode(raw)}")
                    return 1
                rate = sent / (time.time() - start)
                print(f"\r{msgs:>10,} messages, {n:>10,} book, {reports} reports match, "
                      f"{rate / 1e3:6.1f} kB/s", end="", flush=True)
            if last:
                break
        if chunk:
            send(port, b"".join(chunk) + REPORT_REQUEST)
            r = read_report(port, 30)
            reports += 1
            diff = mismatch(r, model, n)
            if diff:
                print(f"\nMISMATCH at the end ({n:,} book messages):\n{diff}")
                return 1

    took = time.time() - start
    ns = 1e9 / CLOCK_HZ
    print(f"\nall {reports} reports match: {msgs:,} messages, {n:,} book messages in {took:.0f} s")
    print(f"board: flags {sorted(r['errors']) or 'none'}, FIFO peak {r['fifo_peak']}, "
          f"{r['lat_n']:,} messages timed, {bad_lines} damaged report lines asked for again")
    if r["lat_n"]:
        mean = r["lat_sum"] / r["lat_n"]
        print(f"latency, last byte to book updated: mean {mean + 1:.1f} clocks ({(mean + 1) * ns:.0f} ns), "
              f"min {r['lat_min'] + 1} ({(r['lat_min'] + 1) * ns:.0f} ns), "
              f"max {r['lat_max'] + 1} ({(r['lat_max'] + 1) * ns:.0f} ns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
