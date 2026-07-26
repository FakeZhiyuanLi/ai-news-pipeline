"""Pipeline stages. Each stage is a module exposing run(run: runs.Run).

Stages never import each other — one exception: check.py may import draft.py
for the retry loop. Enforced by tests/test_architecture.py.
"""

if __name__ == "__main__":
    print("stages package: no stages implemented yet")
