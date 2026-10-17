"""
pro_cli.py — `awos telemetry`, `awos activate`, `awos licence` (self-contained
registration so awos.py only needs one call; see register()).
"""

from __future__ import annotations

import argparse
import sys


def _telemetry(args) -> None:
    from scaffold.agent import usage_ping
    sys.exit(usage_ping.cli(args.telemetry_action))


def _activate(args) -> None:
    from scaffold.agent import licence
    sys.exit(licence.activate(args.key))


def _licence(args) -> None:
    from scaffold.agent import licence
    sys.exit(licence.status_cli())


def register(sub: "argparse._SubParsersAction") -> None:
    """Add the commands; each parser carries its handler as `_pro_handler`."""
    tel = sub.add_parser("telemetry", help="Anonymous usage counts: on, off or status (off by default)")
    tel.add_argument("telemetry_action", nargs="?", default="status", choices=["on", "off", "status"])
    tel.set_defaults(_pro_handler=_telemetry)

    act = sub.add_parser("activate", help="Activate an AWOS Pro licence key")
    act.add_argument("key", metavar="KEY", help="The awos_... key shown after checkout")
    act.set_defaults(_pro_handler=_activate)

    lic = sub.add_parser("licence", aliases=["license"], help="AWOS Pro licence and free-tier usage")
    lic.add_argument("licence_action", nargs="?", default="status", choices=["status"])
    lic.set_defaults(_pro_handler=_licence)
