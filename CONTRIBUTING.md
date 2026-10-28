# Contributing

Thanks for looking. ecadlib is a one-maintainer project, so small, focused changes are the easiest to take.

* **Bugs and wrong parts:** open an [issue](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues) with the part
  number, the datasheet link and what ecadlib built. A wrong footprint or pinout is the most useful report there is.
  When ecadlib itself fails, `ecadlib report-bug` (or **Report a bug** in the web UI) opens an issue filled in with
  the saved error, keys and personal paths blanked out; you see it before you submit.
* **Code:** [docs/development.md](docs/development.md) has the layout, the tests and the accuracy gate. Run
  `python -m tests.run_all` (no pytest needed; `pytest -n 4 tests/` is faster if you have it) before you open a pull
  request, and add a test for what you change. Tests must not import pytest: use `tests/fixtures_lite.py`.
* **No paid calls in tests.** Tests run offline with no API keys; datasheet reads are replayed from saved answers.
* **Licensing:** ecadlib is MIT. Please do not copy code from GPL or AGPL projects, and keep the third-party credits
  described in [NOTICE](NOTICE) and the README.
* **Scope:** Altium (Altium 365 first) and KiCad. Features cut from scope are listed in
  [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md); please ask in an issue before building one of those back.
