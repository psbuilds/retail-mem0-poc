# `scripts/__init__.py`

## 1. File purpose

Marks `scripts` as a package. The diagnostics script is normally run as a file
(`uv run python scripts/check_services.py`), but the marker makes imports predictable and
keeps the repository structure uniform.

## 2. Imports and dependencies

None. Empty so importing the package does not run diagnostics or connect to any
service.

## 3. Classes

None.

## 4. Functions and methods

None.

## 5. Execution workflow

Package import is a no-op. Running the script executes its `main()` under
`if __name__ == "__main__"`, resolving `app.*` imports from the project root.

## 6. Input and output

Input: `import scripts`.
Output: empty package module.

## 7. Interaction with other modules

Contains `check_services.py`, which uses memory, Ollama and product-status helpers.

## 8. Important implementation decisions

- Empty to avoid side effects when tooling imports the package.
- Kept as a package for consistency with the documented project structure.

## 9. Example execution

```bash
uv run python scripts/check_services.py
```
