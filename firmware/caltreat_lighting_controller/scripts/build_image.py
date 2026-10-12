# PlatformIO extra script (pre): firmware version and picotool image steps.
#
# Version: `custom_fw_version = X.Y.Z` in platformio.ini, overridden by the
# LC_FW_VERSION environment variable (test builds). It is compiled in as
# FW_VERSION (0x00XXYYZZ) and sealed into the image's IMAGE_DEF as
# major = X, minor = (Y << 8) | Z, which the boot ROM compares when picking
# between the A and B partitions.
#
# After linking, the ELF is hash-sealed with that version and the UF2 is
# regenerated from it (the platform's own UF2 step runs on the unsealed ELF);
# firmware.bin is built from the sealed ELF afterwards. The partition table
# is written to pt.uf2 next to it.

import os
import re
from os.path import join

Import("env")

picotool = join(
    env.PioPlatform().get_package_dir("tool-picotool-rp2040-earlephilhower") or "",
    "picotool",
)
pt_json = join(env.subst("$PROJECT_DIR"), "partitions.json")
# For upload_command in platformio.ini (scripts/upload.py)
env.Replace(PICOTOOL=picotool)


def parse_version():
    text = os.environ.get("LC_FW_VERSION") or env.GetProjectOption("custom_fw_version", "")
    m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", text)
    if not m or any(int(f) > 255 for f in m.groups()):
        raise ValueError("firmware version must be X.Y.Z with fields 0-255, got %r" % text)
    return tuple(int(f) for f in m.groups())


major, minor, patch = parse_version()
env.Append(CPPDEFINES=[("FW_VERSION", "0x%08X" % ((major << 16) | (minor << 8) | patch))])
print("Firmware version %d.%d.%d" % (major, minor, patch))


def finish_image(target, source, env):
    elf = target[0].get_path()
    uf2 = elf[: -len(".elf")] + ".uf2"
    pt_uf2 = join(os.path.dirname(elf), "pt.uf2")
    cmds = [
        '"%s" seal --quiet --hash --major %d --minor %d "%s" "%s"'
        % (picotool, major, (minor << 8) | patch, elf, elf),
        '"%s" uf2 convert --quiet "%s" -t elf "%s" --family rp2350-arm-s' % (picotool, elf, uf2),
        '"%s" partition create --quiet "%s" "%s"' % (picotool, pt_json, pt_uf2),
    ]
    for cmd in cmds:
        if env.Execute(cmd):
            env.Exit(1)


env.AddPostAction(
    "$BUILD_DIR/${PROGNAME}.elf",
    env.VerboseAction(finish_image, "Sealing image %d.%d.%d with picotool" % (major, minor, patch)),
)
