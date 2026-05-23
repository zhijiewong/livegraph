"""Sample module exercising dynamic dispatch.

``run_operation`` calls ``op`` without static analysis being able to
know that ``op`` is ``Calculator.add`` — only running it reveals that.
"""
from calculator import Calculator


def run_operation(op, a, b):
    return op(a, b)


def main():
    calc = Calculator()
    return run_operation(calc.add, 3, 4)
