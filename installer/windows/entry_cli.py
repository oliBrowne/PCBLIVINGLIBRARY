"""ecadlib.exe in the Windows app: the command line (`ecadlib doctor`, `ecadlib librarian ...`), with a console."""
import multiprocessing, sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from ecadlib.cli import main
    sys.exit(main())
