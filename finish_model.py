#!/usr/bin/env python3
"""Run a script in Blender to finish the model"""

from pathlib import Path
import platform
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType, OptionalValueType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError
from pyapputil.shellutil import Shell

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "blender_file": (StrType(), None),
    "map_image": (StrType(), None),
    "background_image": (StrType(), None),
    "preview_file": (OptionalValueType(StrType()), None),
    "collada_file": (OptionalValueType(StrType()), None),
})
def finish_model(blender_file,
                 map_image,
                 background_image,
                 preview_file,
                 collada_file,
):
    log = GetLogger()

    blender_file = Path(blender_file).resolve()
    if not blender_file.exists():
        raise InvalidArgumentError("Blend file does not exist")

    background_image = Path(background_image).resolve()
    map_image = Path(map_image).resolve()

    if not preview_file:
        preview_file = blender_file.with_name("preview.png")

    if not collada_file:
        collada_file = blender_file.with_suffix(".dae")

    zip_file = blender_file.with_suffix(".zip")

    # Get the current directory to add to the PYTHONPATH for blender
    cwd = Path(sys.path[0]).resolve()

    # Find the blender executable
    if platform.system() == "Darwin":
        blender_path = "/Applications/Blender.app/Contents/MacOS/Blender"
    else:
        log.error("This is currently implemented for macOS only.")
        return False

    parts = [
        f"PYTHONPATH={cwd}",
        blender_path,
        str(blender_file),
        "--python-use-system-env",
        "--python",
        "blender_finish_model.py",
    ]
    if any([map_image, background_image, preview_file, collada_file]):
        parts += ["--"]
    if map_image:
        parts += ["--map-image", str(map_image)]
    if background_image:
        parts += ["--background-image", str(background_image)]
    if preview_file:
        parts += ["--preview-file", str(preview_file)]
    if collada_file:
        parts += ["--collada-file", str(collada_file)]

    cmd = " ".join(parts)
    log.info("Invoking blender...")
    retcode, stdout, stderr = Shell(cmd)
    if stdout:
        log.raw(stdout)

    log.info("Creating archive")
    with ZipFile(zip_file, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(str(collada_file), collada_file.name)
        archive.write(str(background_image), background_image.name)
        archive.write(str(map_image), map_image.name)

    if retcode == 0:
        log.passed("Successfully finished model")
        return True
    else:
        log.error("Failed to finish model")
        log.warning(stderr)
        return False


if __name__ == '__main__':
    parser = ArgumentParser(description="Modify a blender model")
    parser.add_argument("-b", "--blender-file", type=StrType(), metavar="FILENAME", help="Blender model file to modify")
    parser.add_argument("-m", "--map-image", type=StrType(), metavar="FILENAME", help="image to map over the top of the model")
    parser.add_argument("-i", "--background-image", type=StrType(), metavar="FILENAME", help="image to map over the sides and bottom of the model")
    parser.add_argument("-p", "--preview-file", required=False, type=StrType(), metavar="FILENAME", help="path to save preview image")
    parser.add_argument("-c", "--collada-file", required=False, type=StrType(), metavar="FILENAME", help="path to save collada export")

    args = parser.parse_args_to_dict()

    app = PythonApp(finish_model, args)
    app.Run(**args)
