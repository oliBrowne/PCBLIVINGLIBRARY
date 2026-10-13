# BOM triage - `examples/sample_bom.csv`

## Summary

- **needs_footprint_generation**: 10
- **in_library**: 4

## Header mapping

| field | BOM column |
|---|---|
| designator | Designator |
| qty | Qty |
| value | Value |
| footprint | Footprint |
| manufacturer | Manufacturer |
| mpn | Mfr. Part # |
| lcsc | LCSC Part Number |
| description | Description |

## Lines

| # | Ref | MPN | LCSC | Pkg | Status | Local symbol | Local footprint | Src sym/fp/3D | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | U1 | STM32F103C8T6 | C8734 | LQFP48 | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 2 | U2 | AMS1117-3.3 | C6186 | SOT223 | needs_footprint_generation | ecadlib_demo:AMS1117-3.3 (mpn) |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys; local symbol OK but footprint 'Package_TO_SOT_SMD:SOT-223-3_TabPin2' not in indexed libs |
| 3 | R1,R2,R3,R4 | 0603WAF1002T5E | C25804 | 0603 | in_library | ecadlib_demo:R_10k_0603 (mpn) | ecadlib:R_0603_1608Metric | -/-/- |  |
| 4 | C1,C2,C3,C4,C5,C6 | CL05B104KO5NNNC | C1525 | 0402 | in_library | ecadlib_demo:C_100n_0402 (mpn) | ecadlib:C_0402_1005Metric | -/-/- |  |
| 5 | J1 | TYPE-C-31-M-12 | C165948 |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 6 | U3 | ESP32-S3-WROOM-1-N16R8 | C2913202 |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 7 | U4 | TPS62133RGTR | C73973 | VQFN16 | in_library | ecadlib_demo:TPS62133RGTR (mpn) | ecadlib:VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm | -/-/- |  |
| 8 | U5 | CH340C | C84681 | SOIC16 | in_library | ecadlib_demo:CH340C (mpn) | ecadlib:SOIC-16_3.9x9.9mm_P1.27mm | -/-/- |  |
| 9 | Y1 | X322512MSB4SI | C9002 |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 10 | Q1 | AO3401A | C15127 | SOT23 | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 11 | D1 | KT-0603R | C2286 | 0603 | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 12 | J2 | SM04B-SRSS-TB(LF)(SN) | C160404 |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 13 | J3 | FTSH-105-01-L-DV-K |  |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |
| 14 | U6 | TPS62162DSGR |  |  | needs_footprint_generation |  |  | -/-/- | not configured: digikey,mouser,nexar,snapmagic,samacsys |

## Providers

| provider | configured | auth |
|---|---|---|
| digikey | no | OAuth2 client-credentials app from developer.digikey.com (Product Information API v4) |
| mouser | no | Search API key (free, mouser.com/api-hub) passed as ?apiKey= |
| nexar | no | OAuth2 client-credentials (identity.nexar.com) for the Octopart/Nexar GraphQL API; quota-limited |
| snapmagic | no | SnapMagic (ex-SnapEDA) partner API token - no self-serve public API; CAD downloads need a user login |
| samacsys | no | componentsearchengine.com account; access is via Library Loader / partner API, no documented public REST |

## HTTP (this run)

