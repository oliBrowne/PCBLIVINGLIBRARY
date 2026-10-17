# Altium builds the libraries itself

ecadlib writes `.PcbLib` and `.SchLib` files byte by byte (`ecadlib/altium_lib.py`). Altium doesn't document that
format; the layouts come from AltiumSharp and were checked against Altium-saved samples. That's a risk: a new Altium
release could reject a file or quietly misread a field.

So every library ecadlib writes also gets a **recipe** next to it (`<file>.ecadbuild`): the same pads, tracks, pins
and parameters as plain text, in Altium's own units. Inside Altium, a script reads the recipes and creates each
footprint and symbol through Altium's scripting API (`PCBServer` / `SchServer` object factories), then saves the
library. The file you install is then one Altium wrote itself.

## Using it

`ecadlib altium-pkg` does this by default. The install script project it writes (`<name>_Install.PrjScr`) now also
holds `ecadlib_build.pas`, and **InstallDatabaseLibrary**, **InstallFileLibraries** and **BuildIntegratedLibrary**
first run **RebuildWithAltium**: Altium rebuilds every library in the folder from `ecadlib_build.txt`, then installs
it. `--no-native-build` leaves that out.

For any other folder of ecadlib libraries:

```
ecadlib altium-native write D:/libs/ecadlib        # ecadlib_build.txt + ecadlib_build.PrjScr/.pas
# in Altium: File > Open D:/libs/ecadlib/ecadlib_build.PrjScr, File > Run Script > BuildLibrariesInAltium
ecadlib altium-native compare D:/libs/ecadlib      # read back what Altium saved, compare pad by pad and pin by pin
```

`ecadlib_build_report.txt` lists each library as OK or FAILED. `compare` exits 1 on any difference.

## What still uses the byte-level writer

- Altium 365 release (`ecadlib altium365 push`) uploads the files ecadlib wrote, because it runs outside Altium.
  Releasing from inside Altium is the next step.
- Previews, the library hub and the tests read and write the files without Altium.

## Status

Run on Altium Designer 26.10.1 (Windows 11), 2026-10-17, from the command line
(`X2.EXE -RScriptingSystem:RunScript(ProjectName=...|ProcName=...)`) with a test folder of 8 libraries: SOIC-8 and
QFN-32 from package specs, a hand-made through-hole part (square, round and octagonal pads, a plated slot, an
unplated mounting hole, silk track/arc/fill/text, a copper region), a two-footprint PcbLib, extruded 3D bodies and one
embedded STEP model (step_gen's QFN-32), and the four symbols in `examples/symbols`.

- `BuildLibraries`: all 8 libraries OK; `ecadlib altium-native compare`: 0 differences.
- Beyond `compare`: every PCB primitive in Altium's files equals the one ecadlib's byte writer produces for the same
  footprint (pads incl. holes, slots, shapes, corner radius, mask modes; tracks, arcs, fills, regions, texts, bodies,
  the embedded STEP model and its checksum). Altium differs only in values it computes itself: text bounding
  boxes, the cached rule value of a rule-mode mask expansion, track end order and primitive order. Symbols: same
  pins, graphics, parameters and footprint link.
- The package route works: `<name>_Install.pas > RebuildWithAltium` calls `BuildLibraries` in the second unit of
  the same script project (cross-unit call) and rebuilds all 8 libraries; `compare` on the package: 0 differences.

Fixed during that run (the script as first written crashed Altium):
- A pad's `PasteMaskExpansion` / `SolderMaskExpansion` are read-only in Altium 26: assigning them is an access
  violation inside ScriptingSystem.dll that takes Altium down. Mask expansions now go through the pad cache
  (`GetState_Cache`, `PasteMaskExpansionValid := eCacheManual`, `SetState_Cache`).
- The first footprint built in an Altium session kept an invalid pad cache and was saved with no mask expansion mode;
  every pad now gets its cache set (rule: `eCacheValid`).
- A new component has 0 parts: `PartCount` is now always set (Altium saves it as parts + 1, PARTCOUNT=2 for one part,
  as its own libraries do; `compare` now checks it, and the parameters and footprint link).
- Embedded STEP models are written as `ecadlib_3d/<footprint>_<n>/<model name>` so Altium embeds them under the
  model's own name. A STEP file that is missing or that Altium cannot load fails that library (FAILED in the report)
  instead of crashing.
- The OK lines in the report named the kind as `END`.

Notes:
- `BuildLibrariesInAltium` and the install script end with a ShowMessage: fine by hand; for unattended runs call
  `BuildLibraries(Folder)` from another unit.
- A library that fails has already been deleted (it is rebuilt in place): run `ecadlib` again to get the
  byte-written file back.
- Not covered by that run: multi-part symbols and alternate display modes, keep-out regions, bottom-side pads.
