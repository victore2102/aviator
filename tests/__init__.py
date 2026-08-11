"""Marks tests/ as a package so `python -m tests.test_scorer` resolves.

Intentionally empty — importing test modules here would run them on any
`import tests`, and pytest collects the modules directly regardless.
"""
