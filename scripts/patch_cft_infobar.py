#!/usr/bin/env python3
"""
Version-agnostic patcher to remove the Chrome for Testing infobar.

Supports two patterns:

Pattern A (older Chrome, pre-154):
  - Before the enable-automation LEA: test + je that jumps to CfT create
  - Patch: NOP the je to prevent jumping to infobar creation

Pattern B (Chrome 154+):
  - After the enable-automation LEA + call: test + jne that SKIPS infobar creation
  - The fall-through creates the infobar when enable-automation is NOT set
  - Patch: Change jne to unconditional jmp to ALWAYS skip infobar creation
"""
import struct
import shutil
import sys


def find_string_va(data, target):
    """Find virtual address of a string. In CfT, .rodata VA == file offset."""
    off = data.find(target)
    return off if off != -1 else None


def get_text_section(data):
    """Parse ELF to find .text section VA, file offset, and size."""
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3a)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3c)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3e)[0]

    str_sh = e_shoff + e_shstrndx * e_shentsize
    str_off = struct.unpack_from("<Q", data, str_sh + 0x18)[0]

    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        sh_name_idx = struct.unpack_from("<I", data, sh)[0]
        name_start = str_off + sh_name_idx
        name_end = data.index(b'\x00', name_start)
        name = data[name_start:name_end].decode('ascii', errors='replace')
        if name == '.text':
            sh_addr = struct.unpack_from("<Q", data, sh + 0x10)[0]
            sh_offset = struct.unpack_from("<Q", data, sh + 0x18)[0]
            sh_size = struct.unpack_from("<Q", data, sh + 0x20)[0]
            return sh_addr, sh_offset, sh_size
    return None


def find_lea_refs(text, text_va, text_off, target_va):
    """Find all LEA reg,[rip+disp32] instructions referencing target_va."""
    results = []
    for i in range(len(text) - 7):
        if text[i] in (0x48, 0x4c) and text[i+1] == 0x8d:
            modrm = text[i+2]
            if (modrm & 0xc7) == 0x05:
                disp = struct.unpack_from("<i", text, i+3)[0]
                instr_va = text_va + i
                effective_addr = instr_va + 7 + disp
                if effective_addr == target_va:
                    results.append((instr_va, text_off + i, i))
    return results


def find_pattern_a(text, text_va, lea_text_offset):
    """Pattern A: Look backward for test + je jumping to CfT Create (mov edi, small_size)."""
    search_start = max(0, lea_text_offset - 256)
    region = text[search_start:lea_text_offset]

    candidates = []
    for i in range(len(region) - 8):
        b0, b1 = region[i], region[i+1]
        is_test = False
        test_len = 0
        if b0 == 0x45 and b1 == 0x84:
            modrm = region[i+2]
            if (modrm >> 3 & 7) == (modrm & 7) and (modrm & 0xc0) == 0xc0:
                is_test = True
                test_len = 3
        elif b0 == 0x84:
            modrm = b1
            if (modrm >> 3 & 7) == (modrm & 7) and (modrm & 0xc0) == 0xc0:
                is_test = True
                test_len = 2
        elif b0 == 0x40 and b1 == 0x84:
            modrm = region[i+2]
            if (modrm >> 3 & 7) == (modrm & 7) and (modrm & 0xc0) == 0xc0:
                is_test = True
                test_len = 3

        if not is_test:
            continue

        je_pos = i + test_len
        if je_pos + 6 > len(region):
            continue
        if region[je_pos] != 0x0f or region[je_pos+1] != 0x84:
            continue

        je_disp = struct.unpack_from("<i", region, je_pos + 2)[0]
        je_abs_text_off = search_start + je_pos
        je_va = text_va + je_abs_text_off
        target_va = je_va + 6 + je_disp

        target_text_off = target_va - text_va
        if 0 <= target_text_off < len(text) - 5:
            if text[target_text_off] == 0xbf:
                alloc_size = struct.unpack_from("<I", text, target_text_off + 1)[0]
                if alloc_size < 0x200:
                    candidates.append({
                        'pattern': 'A',
                        'je_text_off': je_abs_text_off,
                        'je_va': je_va,
                        'target_va': target_va,
                        'alloc_size': alloc_size,
                        'je_bytes': bytes(region[je_pos:je_pos+6]),
                        'distance': lea_text_offset - je_abs_text_off,
                        'patch_type': 'nop',  # NOP the je to prevent jumping to create
                    })

    return candidates


def find_pattern_b(text, text_va, lea_text_offset):
    """Pattern B (Chrome 154+): Look forward for call + test + jne that skips infobar creation."""
    # The LEA loads "enable-automation" into rsi, then there's a call, then test al,al, then jne
    # We need to find: LEA ... / call ... / test al,al / jne <skip_target>
    # And change jne to unconditional jmp

    search_end = min(len(text), lea_text_offset + 30)
    region = text[lea_text_offset:search_end]

    candidates = []

    # Find the call instruction (E8 xx xx xx xx) after the LEA
    for i in range(7, len(region) - 12):  # Start after LEA (7 bytes)
        if region[i] == 0xe8:  # call rel32
            call_pos = i
            # After call: test al,al (84 c0) then jne (0f 85 xx xx xx xx)
            test_pos = call_pos + 5
            if test_pos + 8 > len(region):
                continue
            if region[test_pos] == 0x84 and region[test_pos+1] == 0xc0:
                jne_pos = test_pos + 2
                if region[jne_pos] == 0x0f and region[jne_pos+1] == 0x85:
                    jne_disp = struct.unpack_from("<i", region, jne_pos + 2)[0]
                    jne_abs_text_off = lea_text_offset + jne_pos
                    jne_va = text_va + jne_abs_text_off
                    target_va = jne_va + 6 + jne_disp

                    candidates.append({
                        'pattern': 'B',
                        'je_text_off': jne_abs_text_off,
                        'je_va': jne_va,
                        'target_va': target_va,
                        'alloc_size': 0,  # Not relevant for pattern B
                        'je_bytes': bytes(region[jne_pos:jne_pos+6]),
                        'distance': jne_pos,  # Distance from LEA
                        'patch_type': 'jmp',  # Change jne to unconditional jmp
                    })
                    break  # Found it

    return candidates


def patch(chrome_path, dry_run=False):
    print(f"Reading {chrome_path}...")
    with open(chrome_path, "rb") as f:
        data = bytearray(f.read())

    target_str = b"enable-automation\x00"
    str_va = find_string_va(data, target_str)
    if str_va is None:
        print("ERROR: Could not find 'enable-automation' string")
        return False
    print(f"  'enable-automation' at VA 0x{str_va:x}")

    text_info = get_text_section(data)
    if text_info is None:
        print("ERROR: Could not find .text section")
        return False
    text_va, text_off, text_size = text_info
    text = data[text_off:text_off + text_size]
    print(f"  .text: VA=0x{text_va:x} offset=0x{text_off:x} size=0x{text_size:x}")

    refs = find_lea_refs(text, text_va, text_off, str_va)
    print(f"  Found {len(refs)} LEA references to 'enable-automation'")

    patches = []
    for ref_va, ref_file_off, ref_text_off in refs:
        # Try Pattern A first (older Chrome)
        candidates = find_pattern_a(text, text_va, ref_text_off)
        for c in candidates:
            je_file_off = text_off + c['je_text_off']
            print(f"  Pattern A candidate: je at VA 0x{c['je_va']:x} (file 0x{je_file_off:x}), "
                  f"target alloc size=0x{c['alloc_size']:x}, distance={c['distance']} bytes before LEA")
            patches.append({**c, 'je_file_off': je_file_off, 'ref_va': ref_va})

        # Try Pattern B (Chrome 154+)
        candidates = find_pattern_b(text, text_va, ref_text_off)
        for c in candidates:
            je_file_off = text_off + c['je_text_off']
            print(f"  Pattern B candidate: jne at VA 0x{c['je_va']:x} (file 0x{je_file_off:x}), "
                  f"distance={c['distance']} bytes after LEA")
            patches.append({**c, 'je_file_off': je_file_off, 'ref_va': ref_va})

    if not patches:
        print("ERROR: Could not find the CfT infobar conditional jump")
        return False

    # Prefer Pattern A if found (more precise), otherwise use ALL Pattern B candidates
    pattern_a = [p for p in patches if p['pattern'] == 'A']
    pattern_b = [p for p in patches if p['pattern'] == 'B']

    if pattern_a:
        pattern_a.sort(key=lambda p: p['distance'])
        to_patch = [pattern_a[0]]
    else:
        # Patch ALL Pattern B candidates - there may be multiple code paths
        to_patch = pattern_b

    if not to_patch:
        print("ERROR: No patches to apply")
        return False

    # Verify all patches before applying any
    for chosen in to_patch:
        actual = bytes(data[chosen['je_file_off']:chosen['je_file_off']+6])
        if actual != chosen['je_bytes']:
            print(f"  ERROR: Byte mismatch at VA 0x{chosen['je_va']:x}! Expected {chosen['je_bytes'].hex()}, got {actual.hex()}")
            return False

    if dry_run:
        for chosen in to_patch:
            print(f"\n  DRY RUN: Would patch Pattern {chosen['pattern']} at VA 0x{chosen['je_va']:x}")
            print(f"    Original bytes: {chosen['je_bytes'].hex()}")
            print(f"    Patch type: {chosen['patch_type']}")
        return True

    backup = chrome_path + ".bak"
    if '--no-backup' not in sys.argv:
        shutil.copy2(chrome_path, backup)
        print(f"  Backup: {backup}")

    # Apply all patches
    for chosen in to_patch:
        print(f"\n  Patching Pattern {chosen['pattern']} at VA 0x{chosen['je_va']:x} (file offset 0x{chosen['je_file_off']:x})")
        print(f"  Original bytes: {chosen['je_bytes'].hex()}")

        if chosen['patch_type'] == 'nop':
            data[chosen['je_file_off']:chosen['je_file_off']+6] = b'\x90' * 6
            print("  Patched: je replaced with 6x NOP")
        else:
            # Pattern B: Change jne/je (0f 85/84) to jmp (e9) - always skip infobar
            # jne rel32: 0f 85 XX XX XX XX (6 bytes)
            # We convert to: NOP + jmp rel32 (6 bytes total)
            # Displacement stays the same since total instruction length is still 6
            disp = struct.unpack_from("<i", chosen['je_bytes'], 2)[0]
            data[chosen['je_file_off']] = 0x90  # NOP
            data[chosen['je_file_off']+1] = 0xe9  # JMP rel32
            struct.pack_into("<i", data, chosen['je_file_off']+2, disp)
            print("  Patched: jne replaced with NOP + JMP (unconditional)")

    with open(chrome_path, "wb") as f:
        f.write(data)

    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chrome = args[0] if args else "/opt/google/chrome/chrome"
    dry = "--dry-run" in sys.argv
    ok = patch(chrome, dry_run=dry)
    sys.exit(0 if ok else 1)
