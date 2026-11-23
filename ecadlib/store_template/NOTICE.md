# Licences of the records in this store

Every record says where its symbol and footprint came from (`symbol.source`, `footprint.source`) and under which terms
(`license`, `credit`). The store's checks refuse any other source.

| Where it came from | `source` | Terms |
|---|---|---|
| Read from the maker's datasheet by ecadlib: pin names, pad sizes, dimensions, and the drawing ecadlib made from them | `datasheet` | MIT (LICENSE). Dimensions and pinouts are facts; the drawing is ecadlib's output |
| ecadlib's standard tables (IPC-7351 lands for chip resistors, SOIC ...) | `standard:...` | MIT (LICENSE) |
| The KiCad libraries (kicad-symbols, kicad-footprints) | `kicad:Library:Name` | CC-BY-SA 4.0 with the KiCad credit kept in the record's `credit`. Anything made from such a record is shared under the same terms, with the credit |
| EasyEDA / LCSC | `easyeda:C...` | A pointer only: the record holds the LCSC number, the hashes of the pads and pins that were seen and the evidence, never the drawing. A machine that uses it fetches the part from EasyEDA itself and compares the hash |

Never stored here, whatever the source:

- maker 3D models (STEP, WRL): a record holds the body height and where a model can be found (`body.model`);
- datasheet PDFs: a record holds the public link and the PDF's SHA-256;
- anything from SnapMagic, SamacSys, Ultra Librarian, a company or personal library, or a part a person picked by
  hand: their terms forbid sharing, or nobody can know who owns it.

KiCad credit: the KiCad symbol and footprint libraries are by the KiCad Library Team and contributors,
https://gitlab.com/kicad/libraries, licensed CC-BY-SA 4.0 with the KiCad libraries exception.

What a record claims: it passed ecadlib's release check, and the evidence lists who built it and how. No automatic
check can prove a footprint matches a datasheet. Check a part against its datasheet before you order boards.
