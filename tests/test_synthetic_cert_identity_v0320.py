from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("scripts/prepare_synthetic_cert_booking.py").read_text(
    encoding="utf-8"
)


def _load_name_function():
    tree = ast.parse(SOURCE)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if "_HEX_TO_ALPHA" in names:
                wanted.append(node)
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name == "_synthetic_given_name"
        ):
            wanted.append(node)

    module = ast.Module(body=wanted, type_ignores=[])
    namespace: dict[str, object] = {}
    exec(compile(module, "<synthetic-name>", "exec"), namespace)
    return namespace["_synthetic_given_name"]


def test_synthetic_given_name_is_stable_and_alphabetic() -> None:
    fn = _load_name_function()

    name = fn("B-20260828-353D6785")

    assert name == fn("B-20260828-353D6785")
    assert name.startswith("CERT")
    assert name.isalpha()
    assert name.isupper()


def test_synthetic_given_name_changes_between_bookings() -> None:
    fn = _load_name_function()

    assert (
        fn("B-20260828-353D6785")
        != fn("B-20260828-D76A0249")
    )
