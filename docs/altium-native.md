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

The script is written from Altium's scripting references and example scripts. It has not yet been run in Altium
Designer: the first run on Altium Designer 26 is tracked in the pull request that added it. Until that run is
recorded here, treat `RebuildWithAltium` as experimental.
