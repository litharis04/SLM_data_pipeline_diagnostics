"""Console scaffold for the ``plgen`` entry point (C01).

Root dispatch, global flags, and the section 9 output/error skeleton.
No command implements behavior yet; every valid command stubs to exit ``5``.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

DEFAULT_WORKSPACE = "./pipeline_workspace"
_MAX_DATA_SEED = 2**63 - 1


def get_version() -> str:
    try:
        return version("data-pipeline-diagnostics")
    except PackageNotFoundError:
        return "0.1.0"


def _parse_data_seed(value: str) -> int:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise argparse.ArgumentTypeError(f"invalid DATA_SEED: {value!r}")
    if not 0 <= parsed <= _MAX_DATA_SEED:
        raise argparse.ArgumentTypeError(
            f"invalid DATA_SEED {parsed}: expected 0 <= DATA_SEED <= 2**63 - 1"
        )
    return parsed


def _parse_max_output_tokens(value: str) -> int:
    try:
        parsed = int(value)
    except TypeError, ValueError:
        raise argparse.ArgumentTypeError(f"invalid --max-output-tokens: {value!r}")
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            f"invalid --max-output-tokens {parsed}: expected a positive integer"
        )
    return parsed


def resolve_workspace(raw: str) -> Path:
    """Resolve a ``--workspace`` value against the invocation directory."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plgen", description="Author local pipelines.")
    parser.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help="Personal workspace location (default: ./pipeline_workspace).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_version()}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    connect = sub.add_parser("connect", help="Configure a provider profile.")
    connect.add_argument("--provider", required=True, help="Provider identifier.")
    connect.add_argument("--model", required=True, help="Model identifier.")
    connect.add_argument("--key-env", default=None, help="Credential env var name.")
    connect.add_argument(
        "--max-output-tokens",
        type=_parse_max_output_tokens,
        default=None,
        help="Per-attempt output budget (positive integer).",
    )

    sub.add_parser("list", help="List personal catalog scenarios.")

    open_parser = sub.add_parser("open", help="Prepare a scenario working copy.")
    open_parser.add_argument("scenario_id", metavar="SCENARIO_ID")

    seed_parser = sub.add_parser("seed", help="Rebuild a scenario with another seed.")
    seed_parser.add_argument("scenario_id", metavar="SCENARIO_ID")
    seed_parser.add_argument(
        "data_seed", metavar="DATA_SEED", type=_parse_data_seed, help="0 <= seed <= 2**63-1."
    )

    create = sub.add_parser("create", help="Author a new scenario.")
    create.add_argument("--domain", required=True, help="Scenario domain.")
    create.add_argument("--id", default=None, help="Scenario identifier.")
    create.add_argument("--size", choices=("small", "medium", "large"), default="small")
    create.add_argument(
        "--composite-keys",
        choices=("auto", "required", "forbidden"),
        default="auto",
    )
    create.add_argument(
        "--staging",
        default=None,
        help="Comma-separated staging ops: trim,lower,upper,replace,map_values,"
        "null_if,coalesce,cast,filter,deduplicate.",
    )
    create.add_argument(
        "--intermediate",
        default=None,
        help="Comma-separated intermediate features: filter,derive,deduplicate.",
    )
    create.add_argument("--joins", choices=("auto", "inner", "left", "mixed"), default="auto")
    create.add_argument(
        "--metrics",
        default=None,
        help="Comma-separated metric functions: count_rows,count,count_distinct,"
        "sum,avg,min,max,conditional_count,conditional_sum.",
    )
    create.add_argument("--seed", type=_parse_data_seed, default=0, help="0 <= seed <= 2**63-1.")

    delete_parser = sub.add_parser("delete", help="Delete a personal scenario.")
    delete_parser.add_argument("scenario_id", metavar="SCENARIO_ID")

    return parser


def dispatch(parsed: argparse.Namespace, workspace: Path) -> int:
    """Stub handler: every valid command is not yet implemented (exit 5)."""
    _ = workspace
    command = getattr(parsed, "command", None)
    print(f"plgen {command}: not yet implemented", file=sys.stderr)
    return 5


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        # --help/--version exit inside parse_args, so the lines below only run
        # for catalog commands. Workspace bootstrap stays library-only at this
        # stage (see workspace.ensure_workspace); nothing here writes files.
        parsed = parser.parse_args(argv)
        workspace = resolve_workspace(parsed.workspace)
        return dispatch(parsed, workspace)
    except KeyboardInterrupt:
        print("plgen: interrupted", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 5


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
