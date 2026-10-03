---
name: testing
summary: Write focused tests that would fail if the behavior broke.
triggers: test, tests, unittest, pytest, coverage, regression, bug, verify, assert
kind: python
---
Rules for this task:
- Test behavior, not implementation. One assertion idea per test, named for the behavior.
- Cover the normal path, one edge case, and one failure case for each function.
- Tests must be deterministic: no network, no clock, no randomness without a seed.
- Show how to run them with one command.
