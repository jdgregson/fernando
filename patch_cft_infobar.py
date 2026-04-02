#!/usr/bin/env python3
"""
Version-agnostic patcher to remove the Chrome for Testing infobar.

Strategy:
1. Find the "enable-automation" string in .rodata (VA = file offset in this section)
2. Find all LEA instructions in .text that reference it
3. For each, walk backward looking for: test <reg>b,<reg>b / je <target>
   where <target> contains mov edi,0x50 (CfT delegate alloc) or a small alloc pattern
4. NOP the je to prevent the infobar from being created

This works because AddInfoBarsIfNecessary always:
  - Calls IsGpuTest() which sets a byte register
  - Tests that register
  - Conditionally jumps to ChromeForTestingInfoBarDelegate::Create()
  - Then falls through to the IsAutomationEnabled() check using "enable-automation"
"""
import struct
import shutil
import sys

def find_string_va(data, target):
    """Find virtual address of a string. In CfT, .rodata VA == file offset."""
    off = data.find(target)
    if off == -1:
        return None
    return off

def get_text_section(data):
    """Parse ELF to find .text section VA, file offset, and size."""
    # ELF header: e_shoff at offset 0x28 (8 bytes), e_shentsize at 0x3a (2 bytes),
    # e_shnum at 0x3c (2 bytes), e_shstrndx at 0x3e (2 bytes)
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3a)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3c)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3e)[0]

    # Get section name string table
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

def find_cft_je(text, text_va, lea_text_offset):
    """Walk backward from the enable-automation LEA to find the je that jumps to CfT Create."""
    # Search up to 256 bytes before the LEA for: test <reg>b,<reg>b (2 bytes) / je rel32 (6 bytes)
    # The je target should contain mov edi,<small_size> (alloc for CfT delegate)
    search_start = max(0, lea_text_offset - 256)
    region = text[search_start:lea_text_offset]

    candidates = []
    for i in range(len(region) - 8):
        b0, b1 = region[i], region[i+1]
        # test r8b-r15b, r8b-r15b: 45 84 XX where XX has matching reg fields
        # test al-bl etc: 84 XX
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

        # Check for je (0x0f 0x84) right after the test
        je_pos = i + test_len
        if je_pos + 6 > len(region):
            continue
        if region[je_pos] != 0x0f or region[je_pos+1] != 0x84:
            continue

        je_disp = struct.unpack_from("<i", region, je_pos + 2)[0]
        je_abs_text_off = search_start + je_pos
        je_va = text_va + je_abs_text_off
        target_va = je_va + 6 + je_disp

        # Verify the jump target looks like CfT Create: starts with mov edi,<size> (bf XX 00 00 00)
        target_text_off = target_va - text_va
        if 0 <= target_text_off < len(text) - 5:
            if text[target_text_off] == 0xbf:
                alloc_size = struct.unpack_from("<I", text, target_text_off + 1)[0]
                if alloc_size < 0x200:  # reasonable object size
                    candidates.append({
                        'je_text_off': je_abs_text_off,
                        'je_va': je_va,
                        'je_file_off': je_abs_text_off + (text_va - (text_va - 0)),  # computed below
                        'target_va': target_va,
                        'alloc_size': alloc_size,
                        'je_bytes': bytes(region[je_pos:je_pos+6]),
                        'distance': lea_text_offset - je_abs_text_off,
                    })

    return candidates

def patch(chrome_path, dry_run=False):
    print(f"Reading {chrome_path}...")
    with open(chrome_path, "rb") as f:
        data = bytearray(f.read())

    # Step 1: Find "enable-automation" string
    target_str = b"enable-automation\x00"
    str_va = find_string_va(data, target_str)
    if str_va is None:
        print("ERROR: Could not find 'enable-automation' string")
        return False
    print(f"  'enable-automation' at VA 0x{str_va:x}")

    # Step 2: Get .text section
    text_info = get_text_section(data)
    if text_info is None:
        print("ERROR: Could not find .text section")
        return False
    text_va, text_off, text_size = text_info
    text = data[text_off:text_off + text_size]
    print(f"  .text: VA=0x{text_va:x} offset=0x{text_off:x} size=0x{text_size:x}")

    # Step 3: Find LEA references to the string
    refs = find_lea_refs(text, text_va, text_off, str_va)
    print(f"  Found {len(refs)} LEA references to 'enable-automation'")

    # Step 4: For each reference, look for the CfT je pattern
    patches = []
    for ref_va, ref_file_off, ref_text_off in refs:
        candidates = find_cft_je(text, text_va, ref_text_off)
        for c in candidates:
            je_file_off = text_off + c['je_text_off']
            print(f"  Candidate: je at VA 0x{c['je_va']:x} (file 0x{je_file_off:x}), "
                  f"target alloc size=0x{c['alloc_size']:x}, "
                  f"distance={c['distance']} bytes before LEA")
            patches.append({**c, 'je_file_off': je_file_off, 'ref_va': ref_va})

    if not patches:
        print("ERROR: Could not find the CfT infobar je instruction")
        return False

    # Pick the best candidate: closest to a LEA ref, with reasonable alloc size
    patches.sort(key=lambda p: p['distance'])
    chosen = patches[0]

    print(f"\n  Patching je at VA 0x{chosen['je_va']:x} (file offset 0x{chosen['je_file_off']:x})")
    print(f"  Original bytes: {chosen['je_bytes'].hex()}")
    print(f"  Jump target: VA 0x{chosen['target_va']:x} (alloc size 0x{chosen['alloc_size']:x})")

    # Verify bytes match
    actual = bytes(data[chosen['je_file_off']:chosen['je_file_off']+6])
    if actual != chosen['je_bytes']:
        print(f"  ERROR: Byte mismatch at file offset! Expected {chosen['je_bytes'].hex()}, got {actual.hex()}")
        return False

    if dry_run:
        print("  DRY RUN: would NOP 6 bytes")
        return True

    # Backup and patch
    backup = chrome_path + ".bak"
    if not sys.argv[-1] == '--no-backup':
        shutil.copy2(chrome_path, backup)
        print(f"  Backup: {backup}")

    data[chosen['je_file_off']:chosen['je_file_off']+6] = b'\x90' * 6
    with open(chrome_path, "wb") as f:
        f.write(data)

    print("  Patched: je replaced with 6x NOP")
    return True

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chrome = args[0] if args else "/opt/google/chrome/chrome"
    dry = "--dry-run" in sys.argv
    ok = patch(chrome, dry_run=dry)
    sys.exit(0 if ok else 1)
