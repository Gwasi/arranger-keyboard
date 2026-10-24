import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(
        0,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "arranger_keyboard"),
    )

from pgui import main

if __name__ == "__main__":
    main()
