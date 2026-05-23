"""Sample test suite for the fixture project."""
from calculator import Calculator
from runner import main, run_operation


def test_add():
    assert Calculator().add(2, 3) == 5


def test_run_operation_dynamic_dispatch():
    assert run_operation(Calculator().multiply, 2, 5) == 10


def test_main():
    assert main() == 7
