"""Build Kodi ZIPs and optionally an HTTP-hosted add-on repository."""
import argparse
import hashlib
from html import escape
from pathlib import Path
from shutil import copyfile
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist"
ADDONS = ("plugin.video.streambeast", "skin.streambeast")


def metadata(folder):
    return ET.parse(folder / "addon.xml").getroot()


def write_zip(folder, target, xml=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        if xml is not None:
            archive.writestr(folder.name + "/addon.xml", xml)
            return
        for source in sorted(folder.rglob("*")):
            relative = source.relative_to(folder)
            if source.is_file() and "__pycache__" not in relative.parts:
                archive.write(source, (Path(folder.name) / relative).as_posix())


def build_repository(base_url, addons):
    output = OUTPUT / "repository"
    root = ET.Element("addons")
    for folder, descriptor, target in addons:
        root.append(descriptor)
        for asset in descriptor.findall(".//assets/*"):
            source = folder / asset.text
            destination = output / target.relative_to(OUTPUT).parent / asset.text
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        destination = output / target.relative_to(OUTPUT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(target.read_bytes())

    index = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    (output / "addons.xml").write_bytes(index)
    (output / "addons.xml.md5").write_text(hashlib.md5(index).hexdigest(), encoding="ascii")
    repo = ET.Element("addon", id="repository.streambeast", name="StreamBeast Repository",
                      version="0.1.0", attrib={"provider-name": "StreamBeast"})
    extension = ET.SubElement(repo, "extension", point="xbmc.addon.repository")
    directory = ET.SubElement(extension, "dir")
    ET.SubElement(directory, "info", compressed="false").text = base_url + "addons.xml"
    ET.SubElement(directory, "checksum").text = base_url + "addons.xml.md5"
    ET.SubElement(directory, "datadir", zip="true").text = base_url.rstrip("/")
    meta = ET.SubElement(repo, "extension", point="xbmc.addon.metadata")
    ET.SubElement(meta, "summary", lang="en_GB").text = "StreamBeast add-on and skin"
    ET.SubElement(meta, "platform").text = "all"
    installer = OUTPUT / "repository.streambeast-0.1.0.zip"
    write_zip(ROOT / "repository.streambeast", installer,
              ET.tostring(repo, encoding="utf-8", xml_declaration=True))
    copyfile(installer, output / installer.name)
    filename = escape(installer.name)
    (output / "index.html").write_text(
        '<!doctype html>\n<html lang="en"><meta charset="utf-8">\n'
        '<title>StreamBeast Kodi Repository</title>\n'
        '<h1>StreamBeast Kodi Repository</h1>\n'
        '<p><a href="{}">{}</a></p>\n</html>\n'.format(filename, filename),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="HTTPS URL at which to host dist/repository/")
    args = parser.parse_args()
    if args.base_url:
        url = urlsplit(args.base_url)
        if url.scheme != "https" or not url.hostname or url.query or url.fragment or url.username:
            parser.error("--base-url must be an HTTPS directory URL")
    addons = []
    for name in ADDONS:
        folder = ROOT / name
        descriptor = metadata(folder)
        target = OUTPUT / name / "{}-{}.zip".format(name, descriptor.attrib["version"])
        write_zip(folder, target)
        addons.append((folder, descriptor, target))
        print(target)
    if args.base_url:
        build_repository(args.base_url.rstrip("/") + "/", addons)


if __name__ == "__main__":
    main()
