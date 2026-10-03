---
name: python-code
summary: Write correct, readable Python that runs on the first try.
triggers: python, function, script, class, algorithm, parse, cli, code, implement, module, library, program
kind: python
---
Rules for this task:
- Standard library first. Add a dependency only when the task needs it, and say so.
- Handle the edge cases the task implies: empty input, bad types, large input. Raise clear errors instead of returning wrong answers.
- Small functions with clear names and type hints. No dead code.
- Include a short runnable example or tests under an `if __name__ == "__main__":` block.
Return the complete code in a single ```python block, then at most three lines of notes.
