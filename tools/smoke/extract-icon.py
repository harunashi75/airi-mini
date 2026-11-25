from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path('.desktop-build/windows-runtime/Lib/site-packages').resolve()))
import pefile

exe, output = sys.argv[1:3]
pe = pefile.PE(exe, fast_load=True)
pe.parse_data_directories([pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
resources = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}

def data(resource):
    language = resource.directory.entries[0].data.struct
    return pe.get_data(language.OffsetToData, language.Size)

icons = {item.id: data(item) for item in resources[3].directory.entries}
group = data(resources[14].directory.entries[0])
count = struct.unpack_from('<H', group, 4)[0]
items = [struct.unpack_from('<BBBBHHIH', group, 6 + i * 14) for i in range(count)]
width, height, colors, reserved, planes, bits, _, index = max(items, key=lambda item: ((item[0] or 256) * (item[1] or 256), item[5]))
pixels = icons[index]
Path(output).write_bytes(struct.pack('<HHHBBBBHHII', 0, 1, 1, width, height, colors, reserved, planes, bits, len(pixels), 22) + pixels)
print(f'{Path(exe).name} : icône native {width or 256} × {height or 256}')
