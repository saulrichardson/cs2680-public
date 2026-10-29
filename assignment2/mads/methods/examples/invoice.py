"""Deliberately incomplete teaching fixture; not production billing code.

Contract: the invoice command prints the sum of integer-cent amounts minus
--discount-cents, floored at zero. With no discount its output is unchanged.
Interface: total_cents(amounts, discount_cents=0) -> int.
"""
import argparse


def total_cents(amounts, discount_cents=0):
    return max(0, sum(amounts) - discount_cents)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('amounts', type=int, nargs='+')
    parser.add_argument('--discount-cents', type=int, default=0)
    args = parser.parse_args()
    # This old consumer does not use the new interface.
    print(sum(args.amounts))


if __name__ == '__main__':
    main()
