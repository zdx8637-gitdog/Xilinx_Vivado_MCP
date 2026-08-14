# G12 — Release Pipeline

> 日期: PLANNED
> 状态: ⬜ Future
> 接手自: G11 (JTAG dev flow working) + G10 (hardware platform)

## Objective

Production deployment: BOOT.BIN generation, QSPI flash programming, SD card boot verification.

## Scope

| Tool | Function |
|------|----------|
| `generate_bootbin` | Create BOOT.BIN from FSBL + bitstream + app |
| `program_flash` | Write BOOT.BIN to QSPI |
| `boot_sd` | Prepare SD card boot image |
| `verify_boot` | Check boot status after power cycle |

## Relationship to Development Flow

- **JTAG** (G9): Development workflow — daily use, fast iteration
- **SD Card** (G12): System validation — verify real boot chain
- **QSPI Flash** (G12): Production — final product deployment

## Bootgen Issue (G10.5)

`-process_bitstream bin` flag causes silent output failure on Windows bootgen v2023.1. Root cause found and documented. This issue belongs to G12 — it does not block the JTAG-based development workflow (G9/G11).
