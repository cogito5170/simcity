"""Run the AI Mayor CLI from any folder:  python C:\\path\\to\\run.py install"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mayor.cli import main  # noqa: E402

sys.exit(main())
