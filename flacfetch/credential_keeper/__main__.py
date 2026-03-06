"""Entry point for running the credential keeper as a module.

Usage: python -m flacfetch.credential_keeper
"""
import asyncio
import sys

from .keeper import run_keeper


def main():
    try:
        asyncio.run(run_keeper())
    except KeyboardInterrupt:
        print("\nCredential keeper stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
