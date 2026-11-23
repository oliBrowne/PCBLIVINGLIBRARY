## Parts in this pull request

<!-- One line per part: maker, MPN, new part or another build of a part already here. -->

## Checklist

- [ ] The records were made by `ecadlib store share` and added with `ecadlib store-repo add . FOLDER` (which also
      updates index/), not edited by hand.
- [ ] I offer these records under this store's licence terms (NOTICE.md): MIT for data ecadlib read or drew, CC-BY-SA
      4.0 with the KiCad credit for parts from the KiCad libraries, a pointer only for EasyEDA parts.
- [ ] No maker 3D model (STEP / WRL), no datasheet PDF and nothing from a library whose terms forbid sharing
      (SnapMagic, SamacSys, Ultra Librarian, a company library).
- [ ] The evidence in this pull request is mine: my GitHub name, my own build.

The `verify` check rebuilds every changed record and runs the release check on it. It cannot read the datasheet, so a
part is only used without a person looking once a second, independent build agrees (or a maintainer signs it off).
