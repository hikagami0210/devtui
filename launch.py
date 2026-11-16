"""Launch the source checkout without changing the caller's working directory."""
from devtui.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
