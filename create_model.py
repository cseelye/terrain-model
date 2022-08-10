#!/usr/bin/env python3
"""Run a script in Blender to finish the model"""

from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType, OptionalValueType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "mesh_file": (StrType(), None),
    "output_file": (StrType(), None),
    "min_thickness": (float, 0.125),
    "size": (float, None),
    "map_image": (StrType(), None),
    "background_image": (StrType(), None),
    "preview_file": (OptionalValueType(StrType()), None),
    "collada_file": (OptionalValueType(StrType()), None),
})
def create_model(mesh_file,
                 output_file,
                 min_thickness,
                 size,
                 map_image,
                 background_image,
                 preview_file,
                 collada_file,
):
    log = GetLogger()

    output_file = Path(output_file).resolve()

    # # Make the texture file paths relative to the blender file path. This makes it work inside and outside the container
    # background_image = Path(background_image).resolve()
    # if not background_image.exists():
    #     raise InvalidArgumentError("Background image file does not exist")
    # map_image = Path(map_image).resolve()
    # if not map_image.exists():
    #     raise InvalidArgumentError("Map image file does not exist")

    # try:
    #     background_image_rel = background_image.relative_to(output_file.parent)
    #     map_image_rel = map_image.relative_to(output_file.parent)
    # except ValueError as ex:
    #     raise InvalidArgumentError("Image files must be relative subpaths to blender file") from ex

    background_image = Path(background_image)
    map_image = Path(map_image)

    if not preview_file:
        preview_file = output_file.with_name("preview.png")

    if not collada_file:
        collada_file = output_file.with_suffix(".dae")
    collada_file = Path(collada_file)

    zip_file = output_file.with_suffix(".zip")

    # Get the current directory to add to the PYTHONPATH for blender
    cwd = Path(sys.path[0]).resolve()

    blender_path = "/opt/blender/blender"
    parts = [
        f"PYTHONPATH={cwd}",
        blender_path,
        "-noaudio",
        "--python-use-system-env",
        "--python",
        "internal_create_model.py",
    ]
    if any([mesh_file, output_file, min_thickness, size, map_image, background_image, preview_file, collada_file]):
        parts += ["--"]
    if mesh_file:
        parts += ["--mesh-file", str(mesh_file)]
    if output_file:
        parts += ["--output-file", str(output_file)]
    if min_thickness:
        parts += ["--min-thickness", str(min_thickness)]
    if size:
        parts += ["--size", str(size)]
    if map_image:
        parts += ["--map-image", str(map_image)]
    if background_image:
        parts += ["--background-image", str(background_image)]
    if preview_file:
        parts += ["--preview-file", str(preview_file)]
    if collada_file:
        parts += ["--collada-file", str(collada_file)]
    # parts += ["-d", "-d"]

    cmd = " ".join(parts)
    log.info("Invoking blender...")

    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
        # Stream the output from blender and the script to the screen
        for line in iter(process.stdout.readline, b''):
            line = line.strip()
            # Detect formatted output from the script and write stdout vs output from blender and send to log
            if re.search(rb"^\d{4}-\d{2}-\d{2}", line):
                sys.stdout.write(line.decode(sys.stdout.encoding) + "\n")
            else:
                log.info(f"  blender: {line.decode('utf-8')}")
        process.wait()
        retcode = process.returncode

    if retcode != 0:
        log.error("Failed to create model")
        return False

    log.info("Creating archive")
    with ZipFile(zip_file, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(str(collada_file), collada_file.name)
        archive.write(str(background_image), background_image.name)
        archive.write(str(map_image), map_image.name)
    log.info(f"Created archive {zip_file}")

    collada_file.unlink()

    log.passed("Successfully created model")
    return True


if __name__ == '__main__':
    parser = ArgumentParser(description="Import x3d/stl mesh and build a nice model")
    parser.add_argument("-m", "--mesh-file", type=StrType(), metavar="FILENAME", help="x3d/stl file to import")
    parser.add_argument("-o", "--output-file", type=StrType(), metavar="FILENAME", help="Blender file to save")
    parser.add_argument("-t", "--min-thickness", type=float, metavar="INCHES", help="Minimum base thickness of the object")
    parser.add_argument("-s", "--size", type=float, metavar="INCHES", help="Size of the long side of the object, other dimensions will be scaled proportionally")
    parser.add_argument("-a", "--map-image", type=StrType(), metavar="FILENAME", help="image to map over the top of the model")
    parser.add_argument("-i", "--background-image", type=StrType(), metavar="FILENAME", help="image to map over the sides and bottom of the model")
    parser.add_argument("-p", "--preview-file", required=False, type=StrType(), metavar="FILENAME", help="path to save preview image")
    parser.add_argument("-c", "--collada-file", required=False, type=StrType(), metavar="FILENAME", help="path to save collada export")

    args = parser.parse_args_to_dict()

    app = PythonApp(create_model, args)
    app.Run(**args)
