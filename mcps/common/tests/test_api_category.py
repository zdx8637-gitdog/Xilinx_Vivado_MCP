"""T-B02-005: API category decorators."""

from mcps.common.api_category import query, set_op, command


@query
def read_something():
    return "data"


@set_op
def update_something():
    return "ok"


@command
def do_something():
    return "accepted"


def test_query_marked():
    assert getattr(read_something, "_api_category") == "query"


def test_set_marked():
    assert getattr(update_something, "_api_category") == "set"


def test_command_marked():
    assert getattr(do_something, "_api_category") == "command"
